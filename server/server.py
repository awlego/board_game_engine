"""
Generic WebSocket game server.

Handles lobby management, player connections, spectators, and routing
commands to pluggable game engines. Knows nothing about specific game rules.
"""

import asyncio
import json
import secrets
import time
from dataclasses import dataclass, field

import websockets

from server import persistence
from server.game_engine import GameEngine


def generate_room_code():
    """Generate a short, human-friendly room code."""
    chars = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"  # no I/O/0/1 for clarity
    return "".join(secrets.choice(chars) for _ in range(5))


def generate_token():
    return secrets.token_urlsafe(24)


@dataclass
class Player:
    player_id: str
    name: str
    token: str
    websocket: object = None
    connected: bool = False


@dataclass
class Spectator:
    token: str
    name: str
    websocket: object = None
    connected: bool = False


@dataclass
class Room:
    code: str
    host_id: str
    engine: GameEngine
    players: dict = field(default_factory=dict)       # player_id -> Player
    spectators: dict = field(default_factory=dict)     # token -> Spectator
    game_state: dict = None
    started: bool = False
    locked: bool = False
    created_at: float = field(default_factory=time.time)
    game_name: str = "unknown"

    @property
    def player_list(self):
        return [
            {
                "player_id": p.player_id, "name": p.name,
                "connected": p.connected, "is_host": p.player_id == self.host_id,
            }
            for p in self.players.values()
        ]


class GameServer:
    """
    Manages rooms, player connections, spectators, and message routing.
    Game-agnostic — delegates all game logic to the engine.
    """

    def __init__(self, data_dir=None):
        self.rooms: dict[str, Room] = {}               # code -> Room
        self.tokens: dict[str, tuple] = {}             # token -> (room_code, player_id_or_"spectator")
        self.engines: dict[str, type] = {}              # game_name -> GameEngine class
        self.data_dir = data_dir                        # room snapshot dir; None = in-memory only

    def register_engine(self, game_name, engine_class):
        """Register a game engine class by name."""
        self.engines[game_name] = engine_class

    # ── Persistence ──────────────────────────────────────────────────

    def _save(self, room):
        """Snapshot a room to disk (no-op when persistence is disabled)."""
        if self.data_dir:
            persistence.save_room(self.data_dir, room)

    def load_persisted_rooms(self):
        """Restore saved rooms. Call after all engines are registered."""
        if not self.data_dir:
            return
        self.rooms, self.tokens = persistence.load_rooms(
            self.data_dir, self.engines, Room, Player, Spectator,
        )
        if self.rooms:
            print(f"Restored {len(self.rooms)} room(s) from {self.data_dir}")

    # ── Room Management ──────────────────────────────────────────────

    def create_room(self, game_name, host_name):
        if game_name not in self.engines:
            raise ValueError(f"Unknown game: {game_name}. Available: {list(self.engines.keys())}")

        code = generate_room_code()
        while code in self.rooms:
            code = generate_room_code()

        engine = self.engines[game_name]()
        player_id = f"p_{generate_token()[:8]}"
        token = generate_token()
        host = Player(player_id=player_id, name=host_name, token=token)

        room = Room(code=code, host_id=player_id, engine=engine, game_name=game_name)
        room.players[player_id] = host

        self.rooms[code] = room
        self.tokens[token] = (code, player_id)
        self._save(room)

        return code, player_id, token

    def join_room(self, code, name):
        room = self.rooms.get(code)
        if room is None:
            raise ValueError(f"Room {code} not found")
        if room.started:
            raise ValueError("Game already in progress")
        if room.locked:
            raise ValueError("Room is locked")
        max_players = room.engine.player_count_range[1]
        if len(room.players) >= max_players:
            raise ValueError("Room is full")

        player_id = f"p_{generate_token()[:8]}"
        token = generate_token()
        player = Player(player_id=player_id, name=name, token=token)
        room.players[player_id] = player
        self.tokens[token] = (code, player_id)
        self._save(room)

        return player_id, token

    def spectate_room(self, code, name):
        room = self.rooms.get(code)
        if room is None:
            raise ValueError(f"Room {code} not found")

        token = generate_token()
        spectator = Spectator(token=token, name=name)
        room.spectators[token] = spectator
        self.tokens[token] = (code, "spectator")
        self._save(room)

        return token

    def start_game(self, code, requester_id):
        room = self.rooms.get(code)
        if room is None:
            raise ValueError("Room not found")
        if room.host_id != requester_id:
            raise ValueError("Only the host can start the game")
        if room.started:
            raise ValueError("Game already started")
        min_players = room.engine.player_count_range[0]
        if len(room.players) < min_players:
            raise ValueError(f"Need at least {min_players} players")

        player_ids = list(room.players.keys())
        player_names = [room.players[pid].name for pid in player_ids]

        room.game_state = room.engine.initial_state(player_ids, player_names)
        room.started = True
        self._save(room)

        return room.game_state

    # ── WebSocket Handler ────────────────────────────────────────────

    async def handle_connection(self, websocket):
        """Main handler for a single WebSocket connection."""
        room_code = None
        player_id = None
        is_spectator = False
        spectator_token = None

        try:
            async for raw in websocket:
                try:
                    msg = json.loads(raw)
                except json.JSONDecodeError:
                    await self._send(websocket, {"type": "error", "message": "Invalid JSON"})
                    continue

                msg_type = msg.get("type")

                # ── Pre-auth messages ────────────────────────────
                if msg_type == "create":
                    await self._handle_create(websocket, msg)
                    continue

                if msg_type == "join":
                    await self._handle_join(websocket, msg)
                    continue

                if msg_type == "spectate":
                    result = await self._handle_spectate(websocket, msg)
                    if result:
                        room_code, spectator_token = result
                        is_spectator = True
                    continue

                if msg_type == "list_rooms":
                    await self._handle_list_rooms(websocket, msg)
                    continue

                if msg_type == "reconnect":
                    result = await self._handle_reconnect(websocket, msg)
                    if result:
                        if result[1] == "spectator":
                            room_code = result[0]
                            spectator_token = result[2]
                            is_spectator = True
                        else:
                            room_code, player_id = result[0], result[1]
                    continue

                if msg_type == "auth":
                    result = await self._handle_auth(websocket, msg)
                    if result:
                        if result[1] == "spectator":
                            room_code = result[0]
                            spectator_token = result[2]
                            is_spectator = True
                        else:
                            room_code, player_id = result[0], result[1]
                    continue

                # ── Spectator messages (limited) ──────────────────
                if is_spectator:
                    if msg_type == "get_state":
                        room = self.rooms.get(room_code)
                        if room:
                            await self._send_spectator_state(room, websocket)
                    else:
                        await self._send(websocket, {"type": "error", "message": "Spectators can only watch"})
                    continue

                # ── Authenticated player messages ─────────────────
                if not room_code or not player_id:
                    await self._send(websocket, {"type": "error", "message": "Not authenticated. Send 'auth' first."})
                    continue

                room = self.rooms.get(room_code)
                if not room:
                    await self._send(websocket, {"type": "error", "message": "Room no longer exists"})
                    continue

                if msg_type == "start":
                    await self._handle_start(room, player_id)

                elif msg_type == "action":
                    await self._handle_action(room, player_id, msg.get("action", {}))

                elif msg_type == "get_state":
                    await self._send_game_state(room, player_id)

                elif msg_type == "chat":
                    await self._broadcast(room, {
                        "type": "chat",
                        "from": room.players[player_id].name,
                        "message": msg.get("message", ""),
                    })

                elif msg_type == "kick":
                    await self._handle_kick(room, player_id, msg)

                elif msg_type == "lock_room":
                    await self._handle_lock(room, player_id, True)

                elif msg_type == "unlock_room":
                    await self._handle_lock(room, player_id, False)

                else:
                    await self._send(websocket, {"type": "error", "message": f"Unknown message type: {msg_type}"})

        except websockets.ConnectionClosed:
            pass
        finally:
            # Mark player or spectator as disconnected
            if is_spectator and room_code and spectator_token:
                room = self.rooms.get(room_code)
                if room and spectator_token in room.spectators:
                    room.spectators[spectator_token].connected = False
                    room.spectators[spectator_token].websocket = None
            elif room_code and player_id:
                room = self.rooms.get(room_code)
                if room and player_id in room.players:
                    room.players[player_id].connected = False
                    room.players[player_id].websocket = None
                    await self._broadcast(room, {
                        "type": "lobby_update",
                        "players": room.player_list,
                        "spectator_count": len([s for s in room.spectators.values() if s.connected]),
                        "reason": f"{room.players[player_id].name} disconnected",
                    })

    # ── Message Handlers ─────────────────────────────────────────────

    async def _handle_create(self, websocket, msg):
        game_name = msg.get("game", "dragon")
        host_name = msg.get("name", "Host")
        try:
            code, player_id, token = self.create_room(game_name, host_name)
            await self._send(websocket, {
                "type": "created",
                "room_code": code,
                "player_id": player_id,
                "token": token,
                "game": game_name,
            })
        except ValueError as e:
            await self._send(websocket, {"type": "error", "message": str(e)})

    async def _handle_join(self, websocket, msg):
        code = msg.get("room_code", "").upper()
        name = msg.get("name", "Player")
        try:
            player_id, token = self.join_room(code, name)
            await self._send(websocket, {
                "type": "joined",
                "room_code": code,
                "player_id": player_id,
                "token": token,
            })
        except ValueError as e:
            await self._send(websocket, {"type": "error", "message": str(e)})

    async def _handle_spectate(self, websocket, msg):
        """Handle a spectate request — join as a non-player observer."""
        code = msg.get("room_code", "").upper()
        name = msg.get("name", "Spectator")
        try:
            token = self.spectate_room(code, name)
            room = self.rooms[code]
            spectator = room.spectators[token]
            spectator.websocket = websocket
            spectator.connected = True

            await self._send(websocket, {
                "type": "spectating",
                "room_code": code,
                "token": token,
            })

            # Send current game state if game is in progress
            if room.started and room.game_state:
                await self._send_spectator_state(room, websocket)

            # Notify room of new spectator
            await self._broadcast(room, {
                "type": "lobby_update",
                "players": room.player_list,
                "spectator_count": len([s for s in room.spectators.values() if s.connected]),
            })

            return code, token
        except ValueError as e:
            await self._send(websocket, {"type": "error", "message": str(e)})
            return None

    async def _handle_list_rooms(self, websocket, msg):
        """List active rooms, optionally filtered by game."""
        game_filter = msg.get("game")
        rooms = []
        for code, room in self.rooms.items():
            if game_filter and room.game_name != game_filter:
                continue
            min_p, max_p = room.engine.player_count_range
            connected_specs = len([s for s in room.spectators.values() if s.connected])
            rooms.append({
                "room_code": code,
                "game": room.game_name,
                "host_name": next((p.name for p in room.players.values() if p.player_id == room.host_id), "?"),
                "player_count": len(room.players),
                "max_players": max_p,
                "started": room.started,
                "joinable": not room.started and not room.locked and len(room.players) < max_p,
                "spectatable": True,
                "locked": room.locked,
                "players": [{"name": p.name, "connected": p.connected} for p in room.players.values()],
                "spectator_count": connected_specs,
            })
        await self._send(websocket, {"type": "room_list", "rooms": rooms})

    async def _handle_auth(self, websocket, msg):
        """Authenticate with a token and bind this websocket to a room/player."""
        token = msg.get("token")
        if not token or token not in self.tokens:
            await self._send(websocket, {"type": "error", "message": "Invalid token"})
            return None

        token_data = self.tokens[token]
        room_code = token_data[0]

        # Check if this is a spectator token
        if token_data[1] == "spectator":
            room = self.rooms.get(room_code)
            if not room or token not in room.spectators:
                await self._send(websocket, {"type": "error", "message": "Spectator session not found"})
                return None

            spectator = room.spectators[token]
            spectator.websocket = websocket
            spectator.connected = True

            await self._send(websocket, {
                "type": "spectating",
                "room_code": room_code,
                "token": token,
            })

            if room.started and room.game_state:
                await self._send_spectator_state(room, websocket)

            return (room_code, "spectator", token)

        # Regular player auth
        player_id = token_data[1]
        room = self.rooms.get(room_code)
        if not room or player_id not in room.players:
            await self._send(websocket, {"type": "error", "message": "Room or player not found"})
            return None

        player = room.players[player_id]
        player.websocket = websocket
        player.connected = True

        await self._send(websocket, {
            "type": "authenticated",
            "room_code": room_code,
            "player_id": player_id,
            "name": player.name,
            "is_host": player_id == room.host_id,
            "game_started": room.started,
        })

        # Broadcast updated player list
        await self._broadcast(room, {
            "type": "lobby_update",
            "players": room.player_list,
            "spectator_count": len([s for s in room.spectators.values() if s.connected]),
            "game_started": room.started,
        })

        # If game is in progress, send current state
        if room.started:
            await self._send_game_state(room, player_id)

        return (room_code, player_id)

    async def _handle_reconnect(self, websocket, msg):
        """Reconnect with a token — delegates to auth."""
        return await self._handle_auth(websocket, msg)

    async def _handle_start(self, room, player_id):
        try:
            self.start_game(room.code, player_id)
            await self._broadcast(room, {
                "type": "game_started",
                "message": "Game has begun!",
            })
            # Send each player their personalized view
            await self._broadcast_game_state(room)
        except ValueError as e:
            player = room.players.get(player_id)
            if player and player.websocket:
                await self._send(player.websocket, {"type": "error", "message": str(e)})

    async def _handle_action(self, room, player_id, action):
        if not room.started or not room.game_state:
            player = room.players.get(player_id)
            if player and player.websocket:
                await self._send(player.websocket, {"type": "error", "message": "Game not started"})
            return

        try:
            result = room.engine.apply_action(room.game_state, player_id, action)
            room.game_state = result.new_state
            self._save(room)

            # Broadcast log to everyone
            if result.log:
                await self._broadcast(room, {
                    "type": "game_log",
                    "messages": result.log,
                })

            # Send updated state to each player
            await self._broadcast_game_state(room)

            if result.game_over:
                await self._broadcast(room, {"type": "game_over"})

        except ValueError as e:
            player = room.players.get(player_id)
            if player and player.websocket:
                await self._send(player.websocket, {"type": "action_error", "message": str(e)})

    async def _handle_kick(self, room, requester_id, msg):
        """Host kicks a player from the lobby (before game starts)."""
        if room.host_id != requester_id:
            player = room.players.get(requester_id)
            if player and player.websocket:
                await self._send(player.websocket, {"type": "error", "message": "Only the host can kick players"})
            return

        if room.started:
            player = room.players.get(requester_id)
            if player and player.websocket:
                await self._send(player.websocket, {"type": "error", "message": "Cannot kick after game started"})
            return

        target_id = msg.get("player_id")
        if target_id == requester_id:
            return  # Can't kick yourself

        target = room.players.get(target_id)
        if not target:
            return

        # Notify the kicked player
        if target.websocket:
            await self._send(target.websocket, {"type": "error", "message": "You have been kicked from the room"})

        # Remove player and invalidate token
        if target.token in self.tokens:
            del self.tokens[target.token]
        del room.players[target_id]
        self._save(room)

        # Broadcast updated player list
        await self._broadcast(room, {
            "type": "lobby_update",
            "players": room.player_list,
            "spectator_count": len([s for s in room.spectators.values() if s.connected]),
        })

    async def _handle_lock(self, room, requester_id, lock):
        """Host locks or unlocks the room."""
        if room.host_id != requester_id:
            player = room.players.get(requester_id)
            if player and player.websocket:
                await self._send(player.websocket, {"type": "error", "message": "Only the host can lock/unlock"})
            return

        room.locked = lock
        self._save(room)
        await self._broadcast(room, {
            "type": "lobby_update",
            "players": room.player_list,
            "locked": room.locked,
            "spectator_count": len([s for s in room.spectators.values() if s.connected]),
        })

    # ── Broadcasting ─────────────────────────────────────────────────

    async def _send(self, websocket, data):
        try:
            await websocket.send(json.dumps(data))
        except websockets.ConnectionClosed:
            pass

    async def _broadcast(self, room, data):
        """Send the same message to all connected players AND spectators."""
        for player in room.players.values():
            if player.connected and player.websocket:
                await self._send(player.websocket, data)
        for spectator in room.spectators.values():
            if spectator.connected and spectator.websocket:
                await self._send(spectator.websocket, data)

    async def _send_game_state(self, room, player_id):
        """Send personalized game view to one player."""
        player = room.players.get(player_id)
        if not player or not player.websocket or not room.game_state:
            return

        view = room.engine.get_player_view(room.game_state, player_id)
        phase_info = room.engine.get_phase_info(room.game_state)
        waiting_for = room.engine.get_waiting_for(room.game_state)

        await self._send(player.websocket, {
            "type": "game_state",
            "state": view,
            "phase_info": phase_info,
            "waiting_for": waiting_for,
            "your_turn": player_id in waiting_for,
        })

    async def _send_spectator_state(self, room, websocket):
        """Send spectator view of game state."""
        if not room.game_state:
            return

        view = room.engine.get_spectator_view(room.game_state)
        phase_info = room.engine.get_phase_info(room.game_state)

        await self._send(websocket, {
            "type": "game_state",
            "state": view,
            "phase_info": phase_info,
            "waiting_for": [],
            "your_turn": False,
        })

    async def _broadcast_game_state(self, room):
        """Send personalized game view to each connected player + spectators."""
        for player_id in room.players:
            await self._send_game_state(room, player_id)
        # Also update spectators
        for spectator in room.spectators.values():
            if spectator.connected and spectator.websocket:
                await self._send_spectator_state(room, spectator.websocket)


# ── Server Entry Point ───────────────────────────────────────────────

async def run_server(host="0.0.0.0", port=8765, data_dir="data/rooms"):
    from server.dragon.engine import DragonEngine
    from server.battleline.engine import BattleLineEngine
    from server.arboretum.engine import ArboretumEngine
    from server.lostcities.engine import LostCitiesEngine
    from server.caylus.engine import CaylusEngine
    from server.tamsk.engine import TamskEngine
    from server.dvonn.engine import DvonnEngine
    from server.yinsh.engine import YinshEngine
    from server.zertz.engine import ZertzEngine
    from server.tzaar.engine import TzaarEngine
    from server.gipf.engine import GipfEngine
    from server.punct.engine import PunctEngine
    from server.lyngk.engine import LyngkEngine
    from server.shards.engine import ShardsEngine

    server = GameServer(data_dir=data_dir)
    server.register_engine("dragon", DragonEngine)
    server.register_engine("battleline", BattleLineEngine)
    server.register_engine("arboretum", ArboretumEngine)
    server.register_engine("lostcities", LostCitiesEngine)
    server.register_engine("caylus", CaylusEngine)
    server.register_engine("tamsk", TamskEngine)
    server.register_engine("dvonn", DvonnEngine)
    server.register_engine("yinsh", YinshEngine)
    server.register_engine("zertz", ZertzEngine)
    server.register_engine("tzaar", TzaarEngine)
    server.register_engine("gipf", GipfEngine)
    server.register_engine("punct", PunctEngine)
    server.register_engine("lyngk", LyngkEngine)
    server.register_engine("shards", ShardsEngine)

    server.load_persisted_rooms()

    print(f"Game server starting on ws://{host}:{port}")
    print(f"Registered games: {list(server.engines.keys())}")

    async with websockets.serve(server.handle_connection, host, port):
        await asyncio.Future()  # run forever


def main():
    """Entry point for run_server.py."""
    import os
    host = os.environ.get("BGE_HOST", "0.0.0.0")
    port = int(os.environ.get("BGE_PORT", "8765"))
    # Set BGE_DATA_DIR="" to disable room persistence.
    data_dir = os.environ.get("BGE_DATA_DIR", "data/rooms") or None
    asyncio.run(run_server(host=host, port=port, data_dir=data_dir))
