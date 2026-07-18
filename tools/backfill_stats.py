#!/usr/bin/env python3
"""Seed the game results store from existing room snapshots.

Usage: python tools/backfill_stats.py [data_dir]     (default: data)

One-off migration: walks data_dir/rooms/*.json and records every
started game into data_dir/stats.db — finished ones (game_over/winner
set in game_state) with their outcome, the rest as in_progress. Each
processed snapshot gets its stats_game_id written back so the live
server links restored rooms to their rows; snapshots that already have
one are skipped, making re-runs safe. Run while the server is stopped.
"""

import importlib
import inspect
import json
import os
import sys
from datetime import datetime, timezone

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from server.game_engine import GameEngine  # noqa: E402
from server.stats import StatsStore  # noqa: E402


def load_engine(game_name):
    module = importlib.import_module(f"server.{game_name}.engine")
    for _, obj in inspect.getmembers(module, inspect.isclass):
        if issubclass(obj, GameEngine) and obj is not GameEngine:
            return obj()
    raise LookupError(f"no GameEngine subclass in server.{game_name}.engine")


def iso(epoch):
    return datetime.fromtimestamp(epoch, timezone.utc).strftime(
        "%Y-%m-%dT%H:%M:%SZ")


class SnapshotRoom:
    """Duck-types the Room/Player fields StatsStore.record_start reads."""

    class Seat:
        def __init__(self, p):
            self.player_id = p["player_id"]
            self.name = p["name"]
            self.username = p.get("username")

    def __init__(self, snap):
        self.code = snap["code"]
        self.game_name = snap["game_name"]
        self.options = snap.get("options")
        self.players = {p["player_id"]: self.Seat(p) for p in snap["players"]}


def main():
    data_dir = sys.argv[1] if len(sys.argv) > 1 else "data"
    rooms_dir = os.path.join(data_dir, "rooms")
    if not os.path.isdir(rooms_dir):
        sys.exit(f"No room snapshots at {rooms_dir}")
    store = StatsStore(os.path.join(data_dir, "stats.db"))

    done = skipped = 0
    for filename in sorted(os.listdir(rooms_dir)):
        if not filename.endswith(".json"):
            continue
        path = os.path.join(rooms_dir, filename)
        with open(path) as f:
            snap = json.load(f)

        if not snap.get("started") or snap.get("stats_game_id") is not None:
            skipped += 1
            continue

        game_id = store.record_start(SnapshotRoom(snap))
        # record_start stamps "now"; correct to the room's creation time.
        with store.db:
            store.db.execute("UPDATE games SET started_at = ? WHERE id = ?",
                             (iso(snap["created_at"]), game_id))

        state = snap.get("game_state") or {}
        finished = bool(state.get("game_over") or state.get("winners")
                        or state.get("winner") is not None)
        if finished:
            engine = load_engine(snap["game_name"])
            store.record_finish(game_id, engine.final_results(state))
            with store.db:
                store.db.execute(
                    "UPDATE games SET finished_at = ? WHERE id = ?",
                    (iso(os.path.getmtime(path)), game_id))

        snap["stats_game_id"] = game_id
        tmp = path + ".tmp"
        with open(tmp, "w") as f:
            json.dump(snap, f)
        os.replace(tmp, path)

        status = "finished" if finished else "in_progress"
        print(f"{snap['code']}: {snap['game_name']} -> game {game_id} ({status})")
        done += 1

    store.close()
    print(f"Backfilled {done} game(s), skipped {skipped} snapshot(s).")


if __name__ == "__main__":
    main()
