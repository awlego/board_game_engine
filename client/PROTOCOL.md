# Game Server Protocol

WebSocket-based protocol for the multiplayer game server.

## Connection Flow

```
Client                          Server
  |                               |
  |── create/join ──────────────>|  (get token)
  |<─ created/joined ───────────|
  |                               |
  |── auth {token} ─────────────>|  (bind connection)
  |<─ authenticated ────────────|
  |<─ lobby_update ─────────────|
  |                               |
  |── start ────────────────────>|  (host only)
  |<─ game_started ─────────────|
  |<─ game_state ───────────────|  (personalized per player)
  |                               |
  |── action {action} ──────────>|  (game moves)
  |<─ game_log ─────────────────|  (broadcast to all)
  |<─ game_state ───────────────|  (personalized per player)
```

## Messages: Client → Server

### `create` — Create a new game room
```json
{"type": "create", "game": "dragon", "name": "Alice"}
```
Response: `created`

### `join` — Join an existing room
```json
{"type": "join", "room_code": "F6PYC", "name": "Bob"}
```
Response: `joined`

### `auth` — Authenticate and bind connection to room/player
```json
{"type": "auth", "token": "..."}
```
Response: `authenticated` + `lobby_update` + (if game started) `game_state`

### `reconnect` — Reconnect after disconnect (same as auth)
```json
{"type": "reconnect", "token": "..."}
```

### `start` — Start the game (host only)
```json
{"type": "start"}
```
Broadcasts: `game_started` + `game_state` to all players

### `action` — Submit a game action
```json
{"type": "action", "action": {"kind": "...", ...}}
```
On success: broadcasts `game_log` + `game_state` to all
On failure: sends `action_error` to the acting player only

### `get_state` — Request current game state
```json
{"type": "get_state"}
```
Response: `game_state`

### `chat` — Send a chat message
```json
{"type": "chat", "message": "Hello!"}
```
Broadcasts: `chat` to all players

### `list_rooms` — Browse active rooms (pre-auth, no token needed)
```json
{"type": "list_rooms", "game": "dvonn"}
```
`game` is optional — omit to list all rooms. Response: `room_list`

### `spectate` — Join a room as a spectator
```json
{"type": "spectate", "room_code": "F6PYC", "name": "Charlie"}
```
Response: `spectating` + (if game started) `game_state` (spectator view)

### `list_card_sets` / `save_card_set` / `delete_card_set` — Custom card sets (pre-auth)
```json
{"type": "list_card_sets", "game": "agricola"}
{"type": "save_card_set", "game": "agricola",
 "set": {"id": "my-set-1a2b", "name": "My Set", "author": "Alice", "cards": ["A001", "..."]}}
{"type": "delete_card_set", "game": "agricola", "id": "my-set-1a2b"}
```
Named card pools a room can deal/draft from instead of whole decks
(built in the set-builder UI, chosen via `options.card_set_id` in
`create` — the server freezes the set's card list into the room's
options at creation). Omit `set.id` when saving to create a new set;
include it to overwrite. Card ids are validated by the game engine's
`validate_card_set` hook (games without one can't save sets). Sets are
shared — there is no ownership. Responses: `card_set_list`
(`{game, sets: [...]}`), `card_set_saved` (`{game, set}` with the
stored record incl. its id), `card_set_deleted` (`{game, id}`).

### `stats` — Play/win statistics (pre-auth, no token needed)
```json
{"type": "stats"}
```
Aggregates from the results store (every started game is recorded at
start, its outcome at game over; games whose room was cleaned up
unfinished count as abandoned). Players are identified by the site
login `username` a trusted reverse proxy appends to the connection URL
(`?user=...`), falling back to the room display name when absent.
Response: `stats` —
```json
{"type": "stats", "enabled": true,
 "players": [{"who": "alex", "game_name": "agricola", "plays": 4,
              "wins": 2, "avg_score": 38.5, "best_score": 47}],
 "games": [{"game_name": "agricola", "starts": 5, "finished": 4,
            "abandoned": 1, "last_played": "2026-07-17T02:11:08Z"}],
 "recent": [{"game_name": "agricola", "finished_at": "2026-07-17T02:11:08Z",
             "players": [{"name": "Alex", "username": "alex",
                          "score": 47, "is_winner": true,
                          "is_bot": false}]}]}
```
Bot seats are recorded (so `recent` shows a game's full seating, with
`is_bot` true) but excluded from the `players` aggregates.
`enabled` is false (all lists empty) when the server runs without a
data dir.

### `add_bot` — Host adds a bot seat to the lobby (before game starts)
```json
{"type": "add_bot"}
```
Adds a server-driven bot player ("Random Bot") to the room. Only games
whose engine implements the `bot_turn` hook accept bots (currently
Agricola; its policy is a random legal action each turn). The server
plays every pending bot turn automatically after the game starts and
after each human action. Bots appear in `lobby_update` player lists
with `"is_bot": true`, can't be authed as, and count toward the
player limit. Remove one pre-start with `kick`. Errors (not host, room
full, game started, unsupported game) come back as `error`.

### `kick` — Host kicks a player from lobby (before game starts)
```json
{"type": "kick", "player_id": "p_def456"}
```

### `lock_room` / `unlock_room` — Host locks/unlocks the room
```json
{"type": "lock_room"}
{"type": "unlock_room"}
```

---

## Messages: Server → Client

### `created`
```json
{
  "type": "created",
  "room_code": "F6PYC",
  "player_id": "p_abc123",
  "token": "...",
  "game": "dragon"
}
```

### `joined`
```json
{
  "type": "joined",
  "room_code": "F6PYC",
  "player_id": "p_def456",
  "token": "..."
}
```

### `authenticated`
```json
{
  "type": "authenticated",
  "room_code": "F6PYC",
  "player_id": "p_abc123",
  "name": "Alice",
  "is_host": true,
  "game_started": false
}
```

### `lobby_update`
```json
{
  "type": "lobby_update",
  "players": [
    {"player_id": "p_abc123", "name": "Alice", "connected": true},
    {"player_id": "p_def456", "name": "Bob", "connected": true}
  ],
  "game_started": false
}
```

### `game_started`
```json
{"type": "game_started", "message": "Game has begun!"}
```

### `game_state` — Personalized game view
```json
{
  "type": "game_state",
  "state": { ... },              // Full game state (with hidden info redacted)
  "phase_info": {
    "phase": "action",
    "round": 0,
    "round_display": 1,
    "total_rounds": 12,
    "description": "Alice is choosing an action"
  },
  "waiting_for": ["p_abc123"],   // Who needs to act
  "your_turn": true              // Is it this player's turn?
}
```

### `game_log`
```json
{
  "type": "game_log",
  "messages": ["Alice: Collected 4¥ (2 base + 2 tax collectors). Now 10¥."]
}
```

### `action_error`
```json
{"type": "action_error", "message": "Not your turn"}
```

### `error`
```json
{"type": "error", "message": "Room not found"}
```

### `game_over`
```json
{"type": "game_over"}
```

### `room_list` — Response to `list_rooms`
```json
{
  "type": "room_list",
  "rooms": [
    {
      "room_code": "F6PYC",
      "game": "dvonn",
      "host_name": "Alice",
      "player_count": 1,
      "max_players": 2,
      "started": false,
      "joinable": true,
      "spectatable": true,
      "locked": false,
      "players": [{"name": "Alice", "connected": true}],
      "spectator_count": 0
    }
  ]
}
```

### `spectating` — Response to `spectate`
```json
{
  "type": "spectating",
  "room_code": "F6PYC",
  "token": "..."
}
```
Spectators receive `game_state`, `game_log`, `game_over`, and `lobby_update` broadcasts.
Spectators cannot submit `action`, `start`, `chat`, or host commands.

---

## Dragon Game Actions

### Draft Phase
```json
{"kind": "draft_pick", "picks": ["monk", "warrior"]}
```

### Action Phase
```json
// Choose an action from a group
{"kind": "choose_action", "group_index": 0, "action_id": "taxes"}
{"kind": "choose_action", "group_index": 1, "action_id": "privilege", "privilege_size": "small"}

// Confirm build placement (after choosing build)
{"kind": "confirm_build", "placement": [
  {"palace_index": 0, "floors": 1},
  {"palace_index": "new", "floors": 1}
]}

// Skip action (top up to 3¥)
{"kind": "skip_action"}
```

### Person Phase
```json
{
  "kind": "play_person",
  "card_index": 2,
  "tile_id": "monk-young-0",
  "palace_index": 0
}

// Replace existing person (when all palaces full)
{
  "kind": "play_person",
  "card_index": 2,
  "tile_id": "monk-young-0",
  "palace_index": 0,
  "replace_index": 1
}

// Release tile immediately (discard without placing)
{
  "kind": "play_person",
  "card_index": 2,
  "tile_id": "monk-young-0",
  "release_immediately": true
}
```

### Event Phase
```json
// Trigger event resolution
{"kind": "resolve_event"}

// Feed palaces during drought
{"kind": "feed_palaces", "fed_palaces": [0, 2]}

// Release a person (drought/contagion/tribute/mongols)
{"kind": "release_person", "palace_index": 1, "person_index": 0}
```

### Scoring Phase
```json
// Calculate scores
{"kind": "score"}

// Advance to next round (or final scoring)
{"kind": "next_round"}
```
