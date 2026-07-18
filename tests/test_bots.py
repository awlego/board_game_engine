"""Tests for server-driven bot seats ("Random Bot"): adding bots to a
room, the GameServer bot-turn loop, snapshot persistence, and resuming
pending bot turns after a restart."""

import asyncio
import json
import random

import pytest

from server import persistence
from server.agricola.bot import random_bot_action, bot_fallback
from server.agricola.engine import AgricolaEngine
from server.server import GameServer, Player, Room, Spectator
from tests.test_stats import ClickerEngine, FakeWebsocket


def make_server(**kwargs):
    server = GameServer(**kwargs)
    server.register_engine("agricola", AgricolaEngine)
    server.register_engine("clicker", ClickerEngine)
    return server


# ── Adding bots ──────────────────────────────────────────────────────

def test_add_bot_makes_a_random_bot_seat():
    server = make_server()
    code, host_id, _ = server.create_room("agricola", "Alex")

    bot = server.add_bot(code, host_id)
    assert bot.name == "Random Bot"
    assert bot.is_bot and bot.connected
    # Nobody can auth as the bot.
    assert bot.token not in server.tokens

    # A second bot gets a distinct name.
    assert server.add_bot(code, host_id).name == "Random Bot 2"

    room = server.rooms[code]
    by_name = {p["name"]: p for p in room.player_list}
    assert by_name["Random Bot"]["is_bot"]
    assert not by_name["Alex"]["is_bot"]


def test_add_bot_validations():
    server = make_server()
    code, host_id, _ = server.create_room("agricola", "Alex")
    guest_id, _ = server.join_room(code, "Brandon")

    with pytest.raises(ValueError, match="host"):
        server.add_bot(code, guest_id)
    with pytest.raises(ValueError, match="not found"):
        server.add_bot("XXXXX", host_id)

    server.add_bot(code, host_id)
    server.add_bot(code, host_id)  # 4 seats: full
    with pytest.raises(ValueError, match="full"):
        server.add_bot(code, host_id)

    server.start_game(code, host_id)
    with pytest.raises(ValueError, match="in progress"):
        server.add_bot(code, host_id)

    # Games without a bot_turn hook refuse bots.
    clicker_code, clicker_host, _ = server.create_room("clicker", "Alex")
    with pytest.raises(ValueError, match="does not support bots"):
        server.add_bot(clicker_code, clicker_host)


def test_handle_add_bot_broadcasts_lobby_update():
    server = make_server()
    code, host_id, _ = server.create_room("agricola", "Alex")
    room = server.rooms[code]
    ws = FakeWebsocket()
    room.players[host_id].websocket = ws
    room.players[host_id].connected = True

    asyncio.run(server._handle_add_bot(room, host_id))
    (msg,) = ws.sent
    assert msg["type"] == "lobby_update"
    assert msg["reason"] == "Random Bot joined"
    assert [p["is_bot"] for p in msg["players"]] == [False, True]

    # Errors go back to the requester instead of raising.
    for _ in range(2):
        asyncio.run(server._handle_add_bot(room, host_id))
    asyncio.run(server._handle_add_bot(room, host_id))
    assert ws.sent[-1] == {"type": "error", "message": "Room is full"}


# ── Bot play through the server ──────────────────────────────────────

def test_bot_plays_a_full_game_through_the_server():
    server = make_server()
    server._bot_rng = random.Random(11)
    rng = random.Random(12)
    random.seed(13)
    code, host_id, _ = server.create_room("agricola", "Alex")
    server.add_bot(code, host_id)
    room = server.rooms[code]

    asyncio.run(server._handle_start(room, host_id))
    engine = room.engine

    steps = 0
    while not room.game_state["game_over"]:
        steps += 1
        assert steps < 3000, "game did not terminate"
        waiting = engine.get_waiting_for(room.game_state)
        assert waiting
        # The server drains every pending bot turn before yielding
        # control back, so the game only ever waits on the human here.
        assert all(not room.players[pid].is_bot for pid in waiting)
        pid = waiting[0]
        action = random_bot_action(engine, room.game_state, pid, rng)
        if action is None:
            action = bot_fallback(engine, room.game_state, pid, rng)
        before = room.game_state
        asyncio.run(server._handle_action(room, pid, action))
        if room.game_state is before:
            # Randomly parameterized action was invalid; the safe
            # fallback must land.
            asyncio.run(server._handle_action(
                room, pid, bot_fallback(engine, room.game_state, pid, rng)))
            assert room.game_state is not before

    assert room.game_state["round"] == 14
    assert len(room.game_state["scores"]) == 2


def test_broken_bot_reports_and_yields_instead_of_crashing():
    server = make_server()
    room, _bot = start_room_waiting_on_bot(server)
    ws = FakeWebsocket()
    room.players[room.host_id].websocket = ws
    room.players[room.host_id].connected = True

    room.engine.bot_turn = lambda state, pid, rng: (_ for _ in ()).throw(
        RuntimeError("boom"))
    asyncio.run(server._run_bots(room))

    stuck = [m for msg in ws.sent if msg["type"] == "game_log"
             for m in msg["messages"] if "stuck" in m]
    assert stuck and "Random Bot" in stuck[0]
    assert room.started and not room.game_state["game_over"]


def test_bot_seats_stay_out_of_the_leaderboard(tmp_path):
    server = make_server(stats_db=str(tmp_path / "stats.db"))
    code, host_id, _ = server.create_room("agricola", "Alex", username="alex")
    bot = server.add_bot(code, host_id)
    server.start_game(code, host_id)
    room = server.rooms[code]

    server.stats.record_finish(room.stats_game_id, {
        "winners": [bot.player_id],
        "scores": {host_id: 20, bot.player_id: 30},
    })
    summary = server.stats.summary()
    # Bots never appear in the per-player aggregates...
    assert [p["who"] for p in summary["players"]] == ["alex"]
    # ...but past games still show their full seating, flagged.
    (recent,) = summary["recent"]
    assert [(p["name"], p["is_bot"], p["is_winner"]) for p in recent["players"]] \
        == [("Alex", False, False), ("Random Bot", True, True)]


# ── Persistence and restart resume ───────────────────────────────────

def start_room_waiting_on_bot(server, tries=64):
    """Start rooms (without running bots) until the bot moves first."""
    for _ in range(tries):
        code, host_id, _ = server.create_room("agricola", "Alex")
        bot = server.add_bot(code, host_id)
        server.start_game(code, host_id)
        room = server.rooms[code]
        if room.engine.get_waiting_for(room.game_state) == [bot.player_id]:
            return room, bot
    pytest.fail("bot never drew the starting seat")


def test_snapshot_roundtrips_bots_and_resume_plays_pending_turns(tmp_path):
    data_dir = str(tmp_path / "rooms")
    server = make_server(data_dir=data_dir)
    room, bot = start_room_waiting_on_bot(server)

    snap = json.load(open(persistence.room_path(data_dir, room.code)))
    assert [p["is_bot"] for p in snap["players"]] == [False, True]

    restarted = make_server(data_dir=data_dir)
    restarted.load_persisted_rooms()
    loaded = restarted.rooms[room.code]
    loaded_bot = loaded.players[bot.player_id]
    assert loaded_bot.is_bot and loaded_bot.connected
    assert bot.token not in restarted.tokens

    # The snapshot was taken with the bot on turn; resume_bots (called
    # on server boot) must play it out until a human is up.
    asyncio.run(restarted.resume_bots())
    waiting = loaded.engine.get_waiting_for(loaded.game_state)
    assert waiting
    assert all(not loaded.players[pid].is_bot for pid in waiting)
