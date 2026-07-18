"""
Abstract game engine interface.

Any board game that plugs into the server must implement this interface.
The server knows nothing about game-specific rules — it just routes
player actions through these methods and broadcasts the results.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any


@dataclass
class ActionResult:
    """Returned by apply_action to tell the server what happened."""
    new_state: dict
    # If non-empty, broadcast a log/message to all players
    log: list[str] = field(default_factory=list)
    # If the game is over after this action
    game_over: bool = False


class GameEngine(ABC):
    """
    Pure-logic game engine. No networking, no rendering — just rules.

    State is always a plain dict (JSON-serializable) so the server can
    store it, send it over the wire, and snapshot it for reconnection.
    """

    # Subclasses can override to restrict player counts.
    player_count_range: tuple[int, int] = (2, 5)

    # Games that support server-driven bot seats override bot_turn with
    # a method (state, player_id, rng) -> ActionResult that picks AND
    # applies one action for that player (like apply_action, except the
    # engine chooses the action). None means no bot support: the server
    # refuses to add bots to the room. bot_name is the display name
    # given to added bot players.
    bot_turn = None
    bot_name: str = "Random Bot"

    @abstractmethod
    def initial_state(self, player_ids: list[str], player_names: list[str]) -> dict:
        """
        Create the starting game state for the given players.
        Called once when a game room starts.
        """
        ...

    @abstractmethod
    def get_player_view(self, state: dict, player_id: str) -> dict:
        """
        Return a filtered/redacted view of the state for one player.
        Hides information that player shouldn't see (other players' hands, etc).
        For fully-open-information games this can just return the full state.
        """
        ...

    @abstractmethod
    def get_valid_actions(self, state: dict, player_id: str) -> list[dict]:
        """
        Return the list of actions this player can currently take.
        Empty list means it's not their turn or they have no choices.
        Each action is a dict describing the action shape the client can submit.
        """
        ...

    @abstractmethod
    def apply_action(self, state: dict, player_id: str, action: dict) -> ActionResult:
        """
        Validate and apply a player's action to the state.
        Returns an ActionResult with the new state.
        Raises ValueError if the action is invalid.
        """
        ...

    @abstractmethod
    def get_waiting_for(self, state: dict) -> list[str]:
        """
        Return list of player_ids who need to act before the game can proceed.
        The server uses this to know who to prompt.
        """
        ...

    @abstractmethod
    def get_phase_info(self, state: dict) -> dict:
        """
        Return a summary of the current phase for display purposes.
        e.g. {"phase": "action", "round": 3, "description": "Choose an action"}
        """
        ...

    def final_results(self, state: dict) -> dict:
        """
        Return the outcome of a finished game for the results store:
        {"winners": [player_id, ...], "scores": {player_id: number} | None}.
        An empty winners list means a draw.

        The default reads the common state conventions — a "winners" list
        or "winner" value (either server player_ids or indices into a
        "players" list of dicts, with "draw"/None meaning no winner), a
        "scores" list of {"player_index", "total", ...} dicts or per-player
        numeric "score" fields. Override if a game stores outcomes
        differently.
        """
        players = state.get("players")

        def to_player_id(w):
            if isinstance(w, int) and isinstance(players, list) \
                    and 0 <= w < len(players) and isinstance(players[w], dict):
                return players[w].get("player_id")
            return w if isinstance(w, str) and w != "draw" else None

        raw = state.get("winners")
        if raw is None:
            raw = [state.get("winner")]
        winners = [pid for pid in (to_player_id(w) for w in raw or []) if pid]

        scores = None
        details = None
        raw_scores = state.get("scores")
        if isinstance(raw_scores, list):
            scores, details = {}, {}
            for s in raw_scores:
                pid = to_player_id(s.get("player_index")) if isinstance(s, dict) else None
                if pid is not None and "total" in s:
                    scores[pid] = s["total"]
                    details[pid] = {k: v for k, v in s.items()
                                    if k not in ("name", "player_index")}
        elif isinstance(raw_scores, dict):
            scores = {pid: v for pid, v in raw_scores.items()
                      if isinstance(v, (int, float))}
        elif isinstance(players, list):
            found = {p.get("player_id"): p["score"] for p in players
                     if isinstance(p, dict) and isinstance(p.get("score"), (int, float))}
            scores = found or None

        return {"winners": winners, "scores": scores or None,
                "score_details": details or None}

    def get_spectator_view(self, state: dict) -> dict:
        """
        Return a view of the state suitable for spectators (non-players).
        Default: full state with no valid_actions and no player identity.
        Games with hidden information (hands, decks) should override this
        to decide what spectators can see.
        """
        from copy import deepcopy
        view = deepcopy(state)
        view["your_player_id"] = None
        view["valid_actions"] = []
        return view
