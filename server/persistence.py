"""
Room persistence.

Snapshots each room to a JSON file after every mutation so in-progress
games survive a server restart. Game state is already a plain JSON dict
and engines are pure logic, so a room serializes to: room metadata +
player/spectator identities (with their reconnect tokens) + the game
state. Live websocket handles are runtime-only and are not saved —
after a restart, clients reconnect with their stored tokens exactly as
they do after a network drop.

Snapshot files also act as room cleanup: files untouched for ROOM_TTL
are deleted (and not loaded) at startup.
"""

import json
import os
import time

# Rooms with no activity for this long are dropped at startup.
ROOM_TTL = 14 * 24 * 60 * 60  # 14 days

FORMAT_VERSION = 1


def room_path(data_dir, code):
    # Room codes are uppercase alphanumeric, so they are filesystem-safe.
    return os.path.join(data_dir, f"{code}.json")


def save_room(data_dir, room):
    """Write an atomic JSON snapshot of one room."""
    snapshot = {
        "version": FORMAT_VERSION,
        "code": room.code,
        "game_name": room.game_name,
        "options": room.options,
        "host_id": room.host_id,
        "started": room.started,
        "locked": room.locked,
        "created_at": room.created_at,
        "stats_game_id": room.stats_game_id,
        "players": [
            {"player_id": p.player_id, "name": p.name, "token": p.token,
             "username": p.username}
            for p in room.players.values()
        ],
        "spectators": [
            {"token": s.token, "name": s.name}
            for s in room.spectators.values()
        ],
        "game_state": room.game_state,
    }
    os.makedirs(data_dir, exist_ok=True)
    path = room_path(data_dir, room.code)
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        json.dump(snapshot, f)
    os.replace(tmp, path)


def delete_room(data_dir, code):
    try:
        os.remove(room_path(data_dir, code))
    except FileNotFoundError:
        pass


def load_rooms(data_dir, engines, room_factory, player_factory, spectator_factory):
    """
    Load all saved rooms. Returns (rooms, tokens) dicts shaped like
    GameServer.rooms / GameServer.tokens. Snapshots that are expired,
    unreadable, or reference an unregistered game are skipped (expired
    ones are deleted).

    The factories are the Room/Player/Spectator dataclasses, passed in
    to avoid a circular import with server.py.
    """
    rooms = {}
    tokens = {}
    if not os.path.isdir(data_dir):
        return rooms, tokens

    now = time.time()
    for filename in sorted(os.listdir(data_dir)):
        if not filename.endswith(".json"):
            continue
        path = os.path.join(data_dir, filename)

        if now - os.path.getmtime(path) > ROOM_TTL:
            os.remove(path)
            continue

        try:
            with open(path) as f:
                snap = json.load(f)
        except (OSError, json.JSONDecodeError) as e:
            print(f"Skipping unreadable room snapshot {filename}: {e}")
            continue

        game_name = snap.get("game_name")
        if game_name not in engines:
            print(f"Skipping room {snap.get('code')}: unknown game {game_name!r}")
            continue

        room = room_factory(
            code=snap["code"],
            host_id=snap["host_id"],
            engine=engines[game_name](),
            game_name=game_name,
            options=snap.get("options"),
            started=snap["started"],
            locked=snap["locked"],
            created_at=snap["created_at"],
            game_state=snap["game_state"],
            stats_game_id=snap.get("stats_game_id"),
        )
        for p in snap["players"]:
            room.players[p["player_id"]] = player_factory(
                player_id=p["player_id"], name=p["name"], token=p["token"],
                username=p.get("username"),
            )
            tokens[p["token"]] = (room.code, p["player_id"])
        for s in snap["spectators"]:
            room.spectators[s["token"]] = spectator_factory(
                token=s["token"], name=s["name"],
            )
            tokens[s["token"]] = (room.code, "spectator")

        rooms[room.code] = room

    return rooms, tokens
