"""Tests for game results/stats: the StatsStore (server/stats.py), the
GameEngine.final_results default hook, GameServer recording at game
start/finish, identity stamping, and snapshot persistence of the new
fields."""

import asyncio
import json

import pytest

from server import persistence
from server.game_engine import ActionResult, GameEngine
from server.server import GameServer, Player, Room, Spectator
from server.stats import StatsStore


class ClickerEngine(GameEngine):
    """Two-player stub: the game ends when anyone acts; actor wins 2-1."""

    player_count_range = (2, 2)

    def initial_state(self, player_ids, player_names):
        return {
            "players": [
                {"player_id": pid, "name": name}
                for pid, name in zip(player_ids, player_names)
            ],
            "winner": None,
        }

    def get_player_view(self, state, player_id):
        return state

    def get_valid_actions(self, state, player_id):
        return [{"type": "win"}]

    def apply_action(self, state, player_id, action):
        new = dict(state)
        new["winner"] = player_id
        new["scores"] = {p["player_id"]: 1 for p in state["players"]}
        new["scores"][player_id] = 2
        return ActionResult(new_state=new, game_over=True)

    def get_waiting_for(self, state):
        return [p["player_id"] for p in state["players"]]

    def get_phase_info(self, state):
        return {"phase": "clicking"}


def make_server(tmp_path, **kwargs):
    server = GameServer(stats_db=str(tmp_path / "stats.db"), **kwargs)
    server.register_engine("clicker", ClickerEngine)
    return server


def start_two_player_game(server, host_user="alex", guest_user="brob"):
    code, host_id, _ = server.create_room("clicker", "Alex", username=host_user)
    guest_id, _ = server.join_room(code, "Brandon", username=guest_user)
    server.start_game(code, host_id)
    return server.rooms[code], host_id, guest_id


# ── StatsStore ───────────────────────────────────────────────────────

def test_store_start_finish_roundtrip(tmp_path):
    server = make_server(tmp_path)
    room, host_id, guest_id = start_two_player_game(server)
    assert room.stats_game_id is not None

    recorded = server.stats.record_finish(room.stats_game_id, {
        "winners": [guest_id], "scores": {host_id: 3, guest_id: 7},
        "score_details": {guest_id: {"total": 7, "bonus": 1}},
    })
    assert recorded

    summary = server.stats.summary()
    assert summary["games"] == [{
        "game_name": "clicker", "starts": 1, "finished": 1, "abandoned": 0,
        "last_played": summary["games"][0]["last_played"],
    }]
    by_who = {p["who"]: p for p in summary["players"]}
    assert by_who["alex"]["plays"] == 1 and by_who["alex"]["wins"] == 0
    assert by_who["brob"]["wins"] == 1 and by_who["brob"]["best_score"] == 7
    (recent,) = summary["recent"]
    assert [p["is_winner"] for p in recent["players"]] == [False, True]
    assert recent["players"][0]["username"] == "alex"


def test_record_finish_is_idempotent(tmp_path):
    server = make_server(tmp_path)
    room, host_id, _ = start_two_player_game(server)
    assert server.stats.record_finish(room.stats_game_id, {"winners": [host_id]})
    # A second game-over is a no-op and must not flip the winner.
    assert not server.stats.record_finish(room.stats_game_id, {"winners": []})
    summary = server.stats.summary()
    assert {p["who"]: p["wins"] for p in summary["players"]}["alex"] == 1


def test_orphan_sweep_only_touches_dead_games(tmp_path):
    server = make_server(tmp_path)
    room_a, host_a, _ = start_two_player_game(server)
    room_b, _, _ = start_two_player_game(server)
    server.stats.record_finish(room_a.stats_game_id, {"winners": [host_a]})

    # room_b survives the "restart"; room_c's room is gone.
    start_two_player_game(server)
    assert server.stats.mark_orphans_abandoned(
        [room_b.stats_game_id, None]) == 1
    rows = [r["status"] for r in server.stats.db.execute(
        "SELECT status FROM games ORDER BY id").fetchall()]
    assert rows == ["finished", "in_progress", "abandoned"]


def test_store_reopen_keeps_schema_and_data(tmp_path):
    server = make_server(tmp_path)
    room, host_id, _ = start_two_player_game(server)
    server.stats.record_finish(room.stats_game_id, {"winners": [host_id]})
    server.stats.close()

    reopened = StatsStore(str(tmp_path / "stats.db"))
    assert reopened.db.execute("PRAGMA user_version").fetchone()[0] == 1
    assert reopened.summary()["games"][0]["finished"] == 1
    reopened.close()


# ── final_results default hook ───────────────────────────────────────

def test_final_results_winner_indices_and_scores_list():
    # Agricola convention: winners are indices, scores a list of dicts.
    engine = ClickerEngine()
    state = {
        "players": [{"player_id": "a"}, {"player_id": "b"}],
        "winners": [1],
        "scores": [
            {"player_index": 0, "name": "A", "total": 20, "fields": 3},
            {"player_index": 1, "name": "B", "total": 25, "fields": 4},
        ],
    }
    results = engine.final_results(state)
    assert results["winners"] == ["b"]
    assert results["scores"] == {"a": 20, "b": 25}
    assert results["score_details"]["b"] == {"total": 25, "fields": 4}


def test_final_results_winner_id_draw_and_score_fallback():
    engine = ClickerEngine()
    # GIPF-family convention: winner is already a player_id.
    assert engine.final_results(
        {"players": [{"player_id": "a"}], "winner": "a"})["winners"] == ["a"]
    # Battleline draw sentinel and None mean no winner.
    assert engine.final_results({"winner": "draw"})["winners"] == []
    assert engine.final_results({"winner": None})["winners"] == []
    # Per-player numeric score fields are picked up when there's no
    # top-level scores key.
    results = engine.final_results({
        "players": [{"player_id": "a", "score": 12},
                    {"player_id": "b", "score": 9}],
        "winner": 0,
    })
    assert results["winners"] == ["a"]
    assert results["scores"] == {"a": 12, "b": 9}


# ── GameServer recording ─────────────────────────────────────────────

def test_game_over_records_result(tmp_path):
    server = make_server(tmp_path)
    room, host_id, guest_id = start_two_player_game(server)

    asyncio.run(server._handle_action(room, guest_id, {"type": "win"}))

    summary = server.stats.summary()
    assert summary["games"][0]["finished"] == 1
    by_who = {p["who"]: p for p in summary["players"]}
    assert by_who["brob"]["wins"] == 1
    assert by_who["brob"]["best_score"] == 2
    assert by_who["alex"]["wins"] == 0

    # Acting again after game over must not double-record.
    asyncio.run(server._handle_action(room, host_id, {"type": "win"}))
    assert server.stats.summary()["games"][0]["finished"] == 1


def test_stats_disabled_server_still_plays(tmp_path):
    server = GameServer()
    server.register_engine("clicker", ClickerEngine)
    room, _, guest_id = start_two_player_game(server)
    assert room.stats_game_id is None
    asyncio.run(server._handle_action(room, guest_id, {"type": "win"}))
    assert room.game_state["winner"] == guest_id


def test_stats_recording_failure_never_breaks_gameplay(tmp_path):
    server = make_server(tmp_path)
    room, _, guest_id = start_two_player_game(server)
    server.stats.close()  # every later stats call now raises
    asyncio.run(server._handle_action(room, guest_id, {"type": "win"}))
    assert room.game_state["winner"] == guest_id


# ── Identity plumbing ────────────────────────────────────────────────

class FakeRequest:
    def __init__(self, path):
        self.path = path


class FakeWebsocket:
    def __init__(self, path=None):
        if path is not None:
            self.request = FakeRequest(path)
        self.sent = []

    async def send(self, data):
        self.sent.append(json.loads(data))


def test_connection_username_parsing():
    assert GameServer._connection_username(
        FakeWebsocket("/?user=alex")) == "alex"
    assert GameServer._connection_username(
        FakeWebsocket("/?user=a%20lex&x=1")) == "a lex"
    assert GameServer._connection_username(FakeWebsocket("/")) is None
    assert GameServer._connection_username(FakeWebsocket()) is None


def test_auth_stamps_username_on_existing_player(tmp_path):
    server = make_server(tmp_path)
    code, host_id, token = server.create_room("clicker", "Alex")
    assert server.rooms[code].players[host_id].username is None

    ws = FakeWebsocket()
    result = asyncio.run(server._handle_auth(ws, {"token": token}, "alex"))
    assert result == (code, host_id)
    assert server.rooms[code].players[host_id].username == "alex"
    assert ws.sent[0]["type"] == "authenticated"


def test_stats_message_returns_summary(tmp_path):
    server = make_server(tmp_path)
    room, host_id, _ = start_two_player_game(server)
    server.stats.record_finish(room.stats_game_id, {"winners": [host_id]})

    ws = FakeWebsocket()
    asyncio.run(server._handle_stats(ws))
    (msg,) = ws.sent
    assert msg["type"] == "stats" and msg["enabled"]
    assert msg["games"][0]["starts"] == 1
    assert {p["who"] for p in msg["players"]} == {"alex", "brob"}

    disabled = GameServer()
    ws2 = FakeWebsocket()
    asyncio.run(disabled._handle_stats(ws2))
    assert ws2.sent[0] == {"type": "stats", "enabled": False,
                           "players": [], "games": [], "recent": []}


# ── Snapshot persistence of new fields ───────────────────────────────

def test_snapshot_roundtrips_username_and_stats_game_id(tmp_path):
    server = make_server(tmp_path, data_dir=str(tmp_path / "rooms"))
    room, host_id, _ = start_two_player_game(server)

    rooms, _ = persistence.load_rooms(
        str(tmp_path / "rooms"), {"clicker": ClickerEngine},
        Room, Player, Spectator)
    loaded = rooms[room.code]
    assert loaded.stats_game_id == room.stats_game_id
    assert loaded.players[host_id].username == "alex"
