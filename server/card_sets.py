"""Named custom card sets (deal/draft pools), shared by all users.

A card set is a curated list of card ids that a room can use INSTEAD of
whole decks as its deal/draft pool: the client sends
options["card_set_id"] at room creation and GameServer.create_room
resolves it into a frozen options["card_pool"] list (plus
options["card_set_name"] for display), so later edits or deletion of
the set never affect a room that already exists.

Sets are game-scoped and stored one JSON file per set under
<directory>/<game>/<id>.json (sibling of the room snapshot dir; see
run_server). With no directory the store is memory-only, matching how
BGE_DATA_DIR="" disables room persistence. There is no per-user
ownership -- the engine never sees the site login, and the audience is
a small trusted group -- so anyone may edit or delete any set.

Validation of the card ids themselves is game-specific: GameServer
passes the engine class's `validate_card_set(card_ids)` staticmethod
(games without one cannot save sets), which returns the normalized
list or raises ValueError.
"""

import json
import os
import re
import secrets
import time

MAX_SETS_PER_GAME = 200
MAX_NAME_LEN = 80
MAX_CARDS = 5000

_ID_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,79}$")
_GAME_RE = re.compile(r"^[a-z0-9_]{1,40}$")


def _slug(name):
    return re.sub(r"-+", "-", re.sub(r"[^a-z0-9]+", "-", name.lower())).strip("-")


class CardSetStore:
    """Load/save/list/delete card sets for `directory`, or in memory
    when directory is None."""

    def __init__(self, directory=None):
        self.directory = directory
        self._memory = {}  # (game, set_id) -> set dict

    # ── Internals ────────────────────────────────────────────────────

    def _game_dir(self, game):
        return os.path.join(self.directory, game)

    def _path(self, game, set_id):
        return os.path.join(self._game_dir(game), f"{set_id}.json")

    @staticmethod
    def _check_game(game):
        if not isinstance(game, str) or not _GAME_RE.match(game):
            raise ValueError("Invalid game name")

    # ── Public API ───────────────────────────────────────────────────

    def list_sets(self, game):
        """All sets for `game` (full records, cards included -- sets are
        a few KB at most), sorted by name."""
        self._check_game(game)
        records = []
        if self.directory is None:
            records = [dict(s) for (g, _), s in self._memory.items()
                       if g == game]
        elif os.path.isdir(self._game_dir(game)):
            for filename in sorted(os.listdir(self._game_dir(game))):
                if not filename.endswith(".json"):
                    continue
                try:
                    with open(os.path.join(self._game_dir(game), filename)) as f:
                        records.append(json.load(f))
                except (OSError, json.JSONDecodeError) as e:
                    print(f"Skipping unreadable card set {filename}: {e}")
        return sorted(records, key=lambda s: (s.get("name", ""), s.get("id", "")))

    def get(self, game, set_id):
        """One set record, or None."""
        self._check_game(game)
        if not isinstance(set_id, str) or not _ID_RE.match(set_id):
            return None
        if self.directory is None:
            record = self._memory.get((game, set_id))
            return dict(record) if record else None
        try:
            with open(self._path(game, set_id)) as f:
                return json.load(f)
        except (OSError, json.JSONDecodeError):
            return None

    def save(self, game, payload, validator):
        """Create or update a set from a client payload ({id?, name,
        author?, cards}). Without an id a fresh one is minted from the
        name (plus a random suffix so same-named sets never clobber
        each other); with an id this overwrites that set. Returns the
        stored record; raises ValueError on any invalid input."""
        self._check_game(game)
        if not isinstance(payload, dict):
            raise ValueError("Malformed card set")

        name = payload.get("name")
        if not isinstance(name, str) or not name.strip():
            raise ValueError("A card set needs a name")
        name = name.strip()[:MAX_NAME_LEN]

        cards = payload.get("cards")
        if not isinstance(cards, list) or not cards or \
                not all(isinstance(c, str) for c in cards):
            raise ValueError("A card set needs a non-empty list of card ids")
        if len(cards) > MAX_CARDS:
            raise ValueError(f"Too many cards (max {MAX_CARDS})")
        cards = list(dict.fromkeys(cards))  # dedupe, keep order
        cards = validator(cards)

        set_id = payload.get("id")
        if set_id is not None:
            if not isinstance(set_id, str) or not _ID_RE.match(set_id):
                raise ValueError("Invalid card set id")
        else:
            set_id = f"{(_slug(name) or 'set')[:60]}-{secrets.token_hex(2)}"
            if len(self.list_sets(game)) >= MAX_SETS_PER_GAME:
                raise ValueError(f"Too many saved sets (max {MAX_SETS_PER_GAME})")

        author = payload.get("author")
        author = author.strip()[:MAX_NAME_LEN] \
            if isinstance(author, str) and author.strip() else None

        record = {"id": set_id, "game": game, "name": name, "cards": cards,
                  "updated_at": time.time()}
        if author:
            record["author"] = author

        if self.directory is None:
            self._memory[(game, set_id)] = record
        else:
            os.makedirs(self._game_dir(game), exist_ok=True)
            path = self._path(game, set_id)
            tmp = path + ".tmp"
            with open(tmp, "w") as f:
                json.dump(record, f)
            os.replace(tmp, path)
        return dict(record)

    def delete(self, game, set_id):
        """Remove a set. Returns True if it existed."""
        self._check_game(game)
        if not isinstance(set_id, str) or not _ID_RE.match(set_id):
            return False
        if self.directory is None:
            return self._memory.pop((game, set_id), None) is not None
        try:
            os.remove(self._path(game, set_id))
            return True
        except FileNotFoundError:
            return False
