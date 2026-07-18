"""
Game results store.

Records every started game and its outcome in a small SQLite database so
win rates, play rates, and per-player history survive room cleanup. The
store is strictly downstream of gameplay: the server wraps every call so
a stats failure can never interrupt a game in progress.

Identity: players carry an optional `username` stamped by a trusted
reverse proxy (see server.py). Without a proxy the column is NULL and
stats fall back to the room display name.
"""

import json
import os
import sqlite3
from datetime import datetime, timezone

# Applied in order at open; PRAGMA user_version tracks progress. Append
# new statements to evolve the schema — never edit shipped entries.
MIGRATIONS = [
    """
    CREATE TABLE games (
        id          INTEGER PRIMARY KEY,
        room_code   TEXT NOT NULL,
        game_name   TEXT NOT NULL,
        options     TEXT,
        started_at  TEXT NOT NULL,
        finished_at TEXT,
        status      TEXT NOT NULL DEFAULT 'in_progress'
                    CHECK (status IN ('in_progress', 'finished', 'abandoned'))
    );

    CREATE TABLE game_players (
        game_id      INTEGER NOT NULL REFERENCES games(id) ON DELETE CASCADE,
        seat         INTEGER NOT NULL,
        player_id    TEXT NOT NULL,
        name         TEXT NOT NULL,
        username     TEXT,
        score        REAL,
        score_detail TEXT,
        is_winner    INTEGER NOT NULL DEFAULT 0,
        PRIMARY KEY (game_id, player_id)
    );

    CREATE INDEX idx_players_username ON game_players(username);
    CREATE INDEX idx_games_name_time  ON games(game_name, started_at);
    """,
]


def _utcnow():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


class StatsStore:
    """Synchronous SQLite store. The server is single-threaded asyncio and
    writes are two tiny statements per game, so no executor is needed."""

    def __init__(self, path):
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        self.db = sqlite3.connect(path)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA journal_mode = WAL")
        self.db.execute("PRAGMA foreign_keys = ON")
        self.db.execute("PRAGMA busy_timeout = 5000")
        self._migrate()

    def _migrate(self):
        version = self.db.execute("PRAGMA user_version").fetchone()[0]
        for i, statements in enumerate(MIGRATIONS[version:], start=version):
            with self.db:
                self.db.executescript(statements)
                self.db.execute(f"PRAGMA user_version = {i + 1}")

    def close(self):
        self.db.close()

    # ── Writes ───────────────────────────────────────────────────────

    def record_start(self, room):
        """Insert a game + its seats. Returns the games.id to stamp on
        the room (persisted in the room snapshot for restart safety)."""
        with self.db:
            cur = self.db.execute(
                "INSERT INTO games (room_code, game_name, options, started_at)"
                " VALUES (?, ?, ?, ?)",
                (room.code, room.game_name,
                 json.dumps(room.options) if room.options else None,
                 _utcnow()),
            )
            game_id = cur.lastrowid
            self.db.executemany(
                "INSERT INTO game_players (game_id, seat, player_id, name, username)"
                " VALUES (?, ?, ?, ?, ?)",
                [
                    (game_id, seat, p.player_id, p.name, p.username)
                    for seat, p in enumerate(room.players.values())
                ],
            )
        return game_id

    def record_finish(self, game_id, results):
        """Mark a game finished and store outcomes. `results` is the dict
        from GameEngine.final_results: {"winners": [player_id, ...],
        "scores": {player_id: number} | None, "score_details":
        {player_id: dict} | None}. Idempotent: a second game-over for the
        same game is a no-op. Returns True if this call recorded it."""
        with self.db:
            cur = self.db.execute(
                "UPDATE games SET finished_at = ?, status = 'finished'"
                " WHERE id = ? AND status = 'in_progress'",
                (_utcnow(), game_id),
            )
            if cur.rowcount == 0:
                return False

            winners = set(results.get("winners") or [])
            scores = results.get("scores") or {}
            details = results.get("score_details") or {}
            for row in self.db.execute(
                "SELECT player_id FROM game_players WHERE game_id = ?", (game_id,)
            ).fetchall():
                pid = row["player_id"]
                self.db.execute(
                    "UPDATE game_players SET score = ?, score_detail = ?,"
                    " is_winner = ? WHERE game_id = ? AND player_id = ?",
                    (scores.get(pid),
                     json.dumps(details[pid]) if pid in details else None,
                     1 if pid in winners else 0, game_id, pid),
                )
        return True

    def mark_orphans_abandoned(self, active_game_ids):
        """Flip in_progress games whose room no longer exists to
        abandoned. Called after room snapshots are restored at boot:
        any in_progress row not backing a live room is unfinishable."""
        ids = [g for g in active_game_ids if g is not None]
        placeholders = ",".join("?" * len(ids))
        with self.db:
            cur = self.db.execute(
                "UPDATE games SET status = 'abandoned' WHERE status = 'in_progress'"
                + (f" AND id NOT IN ({placeholders})" if ids else ""),
                ids,
            )
        return cur.rowcount

    # ── Reads ────────────────────────────────────────────────────────

    def summary(self, recent_limit=20):
        """Aggregate stats for the lobby: per-player records, per-game
        play counts, and the most recent finished games."""
        players = [
            dict(r) for r in self.db.execute(
                """
                SELECT COALESCE(gp.username, gp.name) AS who, g.game_name,
                       COUNT(*) AS plays, SUM(gp.is_winner) AS wins,
                       ROUND(AVG(gp.score), 1) AS avg_score,
                       MAX(gp.score) AS best_score
                FROM game_players gp JOIN games g ON g.id = gp.game_id
                WHERE g.status = 'finished'
                GROUP BY who, g.game_name
                ORDER BY who, g.game_name
                """
            ).fetchall()
        ]
        games = [
            dict(r) for r in self.db.execute(
                """
                SELECT game_name, COUNT(*) AS starts,
                       SUM(status = 'finished') AS finished,
                       SUM(status = 'abandoned') AS abandoned,
                       MAX(started_at) AS last_played
                FROM games GROUP BY game_name ORDER BY starts DESC
                """
            ).fetchall()
        ]
        recent = []
        for g in self.db.execute(
            "SELECT * FROM games WHERE status = 'finished'"
            " ORDER BY finished_at DESC LIMIT ?", (recent_limit,)
        ).fetchall():
            seats = self.db.execute(
                "SELECT name, username, score, is_winner FROM game_players"
                " WHERE game_id = ? ORDER BY seat", (g["id"],)
            ).fetchall()
            recent.append({
                "game_name": g["game_name"],
                "finished_at": g["finished_at"],
                "players": [
                    {"name": s["name"], "username": s["username"],
                     "score": s["score"], "is_winner": bool(s["is_winner"])}
                    for s in seats
                ],
            })
        return {"players": players, "games": games, "recent": recent}
