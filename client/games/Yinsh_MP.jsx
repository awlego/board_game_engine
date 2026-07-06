import React, { useState, useEffect, useRef, useCallback, useMemo } from "react";

// ─── CONFIGURATION ─────────────────────────────────────────────────
import { WS_URL } from "../ws.js";

// ─── CONSTANTS ─────────────────────────────────────────────────────

const AXIAL_DIRS = [[1,0],[-1,0],[0,1],[0,-1],[1,-1],[-1,1]];
const EXCLUDED_CORNERS = new Set(["5,0","-5,0","0,5","0,-5","5,-5","-5,5"]);

const PIECE_STYLES = {
  white: { ring: "#e0d8c8", marker: "#e8e0d0", stroke: "#999", text: "#333" },
  black: { ring: "#3a4a5a", marker: "#2c3e50", stroke: "#1a252f", text: "#e8d5a3" },
};

const HEX_SIZE = 24;
const SQRT3 = Math.sqrt(3);

// ─── HEX MATH ──────────────────────────────────────────────────────

function hexKey(q, r) { return `${q},${r}`; }
function parseHex(key) { const [q, r] = key.split(",").map(Number); return [q, r]; }

function isValid(q, r) {
  if (Math.max(Math.abs(q), Math.abs(r), Math.abs(q + r)) > 5) return false;
  return !EXCLUDED_CORNERS.has(hexKey(q, r));
}

function hexToPixel(q, r) {
  return {
    x: HEX_SIZE * 1.5 * q,
    y: HEX_SIZE * (SQRT3 / 2 * q + SQRT3 * r),
  };
}

function allPositions() {
  const positions = [];
  for (let q = -5; q <= 5; q++) {
    for (let r = -5; r <= 5; r++) {
      if (isValid(q, r)) positions.push([q, r]);
    }
  }
  return positions;
}

const ALL_POSITIONS = allPositions();

// Grid lines: connect each point to its neighbors in 3 directions
function gridLines() {
  const lines = [];
  const posSet = new Set(ALL_POSITIONS.map(([q,r]) => hexKey(q,r)));
  const dirs = [[1,0],[0,1],[1,-1]]; // 3 directions (avoid duplicates)
  for (const [q, r] of ALL_POSITIONS) {
    for (const [dq, dr] of dirs) {
      const nq = q + dq, nr = r + dr;
      if (posSet.has(hexKey(nq, nr))) {
        const from = hexToPixel(q, r);
        const to = hexToPixel(nq, nr);
        lines.push({ x1: from.x, y1: from.y, x2: to.x, y2: to.y });
      }
    }
  }
  return lines;
}

const GRID_LINES = gridLines();

// ─── THEME & STYLES ────────────────────────────────────────────────

const font = `'Cinzel', Georgia, serif`;
const S = {
  app: {
    fontFamily: font, minHeight: "100vh",
    background: "linear-gradient(160deg, #0d1117 0%, #161b22 30%, #0d1117 100%)",
    color: "#e8d5a3", position: "relative", overflow: "hidden",
  },
  overlay: {
    position: "fixed", inset: 0, pointerEvents: "none", zIndex: 0,
    backgroundImage: `repeating-linear-gradient(45deg, transparent, transparent 35px, rgba(201,168,76,0.02) 35px, rgba(201,168,76,0.02) 70px)`,
  },
  content: { position: "relative", zIndex: 1, maxWidth: 1100, margin: "0 auto", padding: "16px 20px" },
  card: {
    background: "linear-gradient(135deg, rgba(22,27,34,0.95) 0%, rgba(13,17,23,0.98) 100%)",
    border: "1px solid #30363d", borderRadius: 8, padding: 20, marginBottom: 16,
    boxShadow: "0 4px 20px rgba(0,0,0,0.5)",
  },
  cardTitle: {
    fontFamily: font, fontSize: 18, color: "#c9a84c",
    marginBottom: 12, borderBottom: "1px solid #30363d", paddingBottom: 8,
  },
  btn: {
    fontFamily: font, fontSize: 14, padding: "8px 20px", borderRadius: 6,
    border: "1px solid #30363d",
    background: "linear-gradient(135deg, #21262d 0%, #161b22 100%)",
    color: "#e8d5a3", cursor: "pointer", transition: "all 0.2s", fontWeight: 600,
  },
  btnP: { background: "linear-gradient(135deg, #c9a84c 0%, #a08030 100%)", color: "#0d1117", border: "1px solid #c9a84c" },
  dis: { opacity: 0.4, cursor: "not-allowed" },
  title: {
    fontFamily: font, fontSize: 36, fontWeight: 700, color: "#c9a84c",
    textShadow: "0 2px 8px rgba(0,0,0,0.5)", margin: 0, letterSpacing: 3,
  },
  input: {
    flex: 1, fontFamily: font, fontSize: 14, padding: "8px 12px",
    borderRadius: 6, border: "1px solid #30363d",
    background: "rgba(0,0,0,0.3)", color: "#e8d5a3", outline: "none",
  },
};

function bs(primary, disabled) {
  return { ...S.btn, ...(primary ? S.btnP : {}), ...(disabled ? S.dis : {}) };
}

// ─── UTILITIES ─────────────────────────────────────────────────────

function getMyPlayerIdx(state) {
  if (!state || !state.players) return 0;
  const idx = state.players.findIndex(p => p.player_id === state.your_player_id);
  return idx >= 0 ? idx : 0;
}

// ─── WEBSOCKET HOOK ────────────────────────────────────────────────

function useGameConnection() {
  const [connected, setConnected] = useState(false);
  const [roomCode, setRoomCode] = useState(null);
  const [playerId, setPlayerId] = useState(null);
  const [token, setToken] = useState(null);
  const [isHost, setIsHost] = useState(false);
  const [lobby, setLobby] = useState([]);
  const [gameStarted, setGameStarted] = useState(false);
  const [gameState, setGameState] = useState(null);
  const [phaseInfo, setPhaseInfo] = useState(null);
  const [yourTurn, setYourTurn] = useState(false);
  const [waitingFor, setWaitingFor] = useState([]);
  const [gameLogs, setGameLogs] = useState([]);
  const [gameOver, setGameOver] = useState(false);
  const [error, setError] = useState(null);

  const wsRef = useRef(null);
  const tokenRef = useRef(null);

  const send = useCallback((msg) => {
    if (wsRef.current?.readyState === WebSocket.OPEN)
      wsRef.current.send(JSON.stringify(msg));
  }, []);

  const connect = useCallback((onOpen) => {
    if (wsRef.current?.readyState === WebSocket.OPEN) { onOpen?.(); return; }
    const ws = new WebSocket(WS_URL);
    wsRef.current = ws;

    ws.onopen = () => {
      setConnected(true);
      setError(null);
      if (tokenRef.current)
        ws.send(JSON.stringify({ type: "reconnect", token: tokenRef.current }));
      onOpen?.();
    };

    ws.onmessage = (evt) => {
      const msg = JSON.parse(evt.data);
      switch (msg.type) {
        case "created":
        case "joined":
          setRoomCode(msg.room_code);
          setPlayerId(msg.player_id);
          setToken(msg.token);
          tokenRef.current = msg.token;
          sessionStorage.setItem("game_token", msg.token);
          ws.send(JSON.stringify({ type: "auth", token: msg.token }));
          break;
        case "authenticated":
          setRoomCode(msg.room_code);
          setPlayerId(msg.player_id);
          setIsHost(msg.is_host);
          setGameStarted(msg.game_started);
          break;
        case "lobby_update":
          setLobby(msg.players);
          if (msg.game_started !== undefined) setGameStarted(msg.game_started);
          break;
        case "game_started":
          setGameStarted(true);
          break;
        case "game_state":
          setGameState(msg.state);
          setPhaseInfo(msg.phase_info);
          setYourTurn(msg.your_turn);
          setWaitingFor(msg.waiting_for || []);
          break;
        case "game_log":
          setGameLogs((prev) => [...prev, ...msg.messages]);
          break;
        case "game_over":
          setGameOver(true);
          break;
        case "action_error":
          setError(msg.message);
          break;
        case "error":
          setError(msg.message);
          break;
      }
    };

    ws.onclose = () => {
      setConnected(false);
      setTimeout(() => { if (tokenRef.current) connect(); }, 2000);
    };
  }, []);


  const createRoom = (name) => {
    connect(() => send({ type: "create", game: "yinsh", name }));
  };
  const joinRoom = (code, name) => {
    connect(() => send({ type: "join", room_code: code.toUpperCase(), name }));
  };

  // Auto-create/join from main menu (reads pending_action from sessionStorage)
  useEffect(() => {
    const pending = sessionStorage.getItem("pending_action");
    if (pending && !tokenRef.current) {
      try {
        const { roomCode, playerName } = JSON.parse(pending);
        sessionStorage.removeItem("pending_action");
        if (roomCode) {
          joinRoom(roomCode, playerName);
        } else {
          createRoom(playerName);
        }
      } catch (e) {
        sessionStorage.removeItem("pending_action");
      }
    }
  }, []);

  const startGame = () => send({ type: "start" });
  const submitAction = (action) => send({ type: "action", action });

  return {
    connected, roomCode, playerId, token, isHost, lobby,
    gameStarted, gameState, phaseInfo, yourTurn, waitingFor,
    gameLogs, gameOver, error,
    createRoom, joinRoom, startGame, submitAction,
  };
}

// ─── BOARD POINT COMPONENT ─────────────────────────────────────────

function BoardPoint({ q, r, cell, isValidTarget, isSelected, isInRow, onClick }) {
  const { x, y } = hexToPixel(q, r);
  const ringR = HEX_SIZE * 0.65;
  const markerR = HEX_SIZE * 0.45;

  let cursor = onClick ? "pointer" : "default";

  return (
    <g onClick={onClick} style={{ cursor }}>
      {/* Empty dot */}
      {!cell && !isValidTarget && (
        <circle cx={x} cy={y} r={3} fill="#444" />
      )}
      {/* Valid target highlight */}
      {isValidTarget && !cell && (
        <circle cx={x} cy={y} r={8} fill="rgba(76,175,80,0.3)" stroke="#4caf50" strokeWidth={2} />
      )}
      {/* Row highlight */}
      {isInRow && (
        <circle cx={x} cy={y} r={ringR + 4} fill="none" stroke="#f1c40f" strokeWidth={2.5}
          strokeDasharray="4 3" opacity={0.9} />
      )}
      {/* Marker */}
      {cell && cell.type === "marker" && (
        <circle
          cx={x} cy={y} r={markerR}
          fill={PIECE_STYLES[cell.color].marker}
          stroke={isSelected ? "#ffeb3b" : PIECE_STYLES[cell.color].stroke}
          strokeWidth={isSelected ? 3 : 1.5}
        />
      )}
      {/* Ring */}
      {cell && cell.type === "ring" && (
        <>
          <circle
            cx={x} cy={y} r={ringR}
            fill="none"
            stroke={isSelected ? "#ffeb3b" : PIECE_STYLES[cell.color].ring}
            strokeWidth={isSelected ? 5 : 4}
          />
          {/* Inner hollow */}
          <circle
            cx={x} cy={y} r={ringR - 5}
            fill="none"
            stroke={PIECE_STYLES[cell.color].ring}
            strokeWidth={1}
            opacity={0.4}
          />
        </>
      )}
      {/* Valid target on occupied space (for ring placement during setup) */}
      {isValidTarget && cell && (
        <circle cx={x} cy={y} r={ringR + 4} fill="none" stroke="#4caf50" strokeWidth={2.5} />
      )}
    </g>
  );
}

// ─── HEX BOARD COMPONENT ──────────────────────────────────────────

function HexBoard({ state, submitAction, myColor }) {
  const board = state.board;
  const validActions = state.valid_actions || [];
  const phase = state.phase;
  const subPhase = state.sub_phase;
  const isMyTurn = useMemo(() => {
    const myIdx = getMyPlayerIdx(state);
    // During remove_row/remove_ring, row_player determines who acts
    if (subPhase === "remove_row" || subPhase === "remove_ring") {
      return state.row_player === myIdx;
    }
    return state.current_player === myIdx;
  }, [state]);

  const [selectedRing, setSelectedRing] = useState(null);

  // Reset selection on phase changes
  useEffect(() => { setSelectedRing(null); }, [subPhase, state.current_player]);

  // Compute valid targets based on current phase
  const validTargets = useMemo(() => {
    const targets = new Set();
    if (phase === "placement") {
      validActions.filter(a => a.kind === "place_ring").forEach(a => targets.add(a.position));
    } else if (subPhase === "place_marker") {
      validActions.filter(a => a.kind === "place_marker").forEach(a => targets.add(a.ring));
    } else if (subPhase === "move_ring") {
      validActions.filter(a => a.kind === "move_ring").forEach(a => targets.add(a.to));
    } else if (subPhase === "remove_ring") {
      validActions.filter(a => a.kind === "remove_ring").forEach(a => targets.add(a.ring));
    }
    return targets;
  }, [phase, subPhase, validActions]);

  // Pending rows highlight
  const pendingRowPositions = useMemo(() => {
    const positions = new Set();
    const myIdx = getMyPlayerIdx(state);
    const rows = state.row_player === myIdx
      ? (state.pending_rows || [])
      : (state.opponent_pending_rows || []);
    // Only highlight during remove_row phase
    if (subPhase === "remove_row") {
      rows.forEach(row => row.forEach(k => positions.add(k)));
    }
    return positions;
  }, [state, subPhase]);

  const handleClick = useCallback((key) => {
    if (!isMyTurn) return;

    if (phase === "placement") {
      if (validTargets.has(key)) {
        submitAction({ kind: "place_ring", position: key });
      }
      return;
    }

    if (subPhase === "place_marker") {
      if (validTargets.has(key)) {
        submitAction({ kind: "place_marker", ring: key });
      }
      return;
    }

    if (subPhase === "move_ring") {
      if (validTargets.has(key)) {
        submitAction({ kind: "move_ring", to: key });
      }
      return;
    }

    if (subPhase === "remove_row") {
      // Find which row contains this position
      const myIdx = getMyPlayerIdx(state);
      const rows = state.row_player === myIdx
        ? (state.pending_rows || [])
        : (state.opponent_pending_rows || []);
      const matchingRow = rows.find(row => row.includes(key));
      if (matchingRow) {
        submitAction({ kind: "select_row", row: matchingRow });
      }
      return;
    }

    if (subPhase === "remove_ring") {
      if (validTargets.has(key)) {
        submitAction({ kind: "remove_ring", ring: key });
      }
      return;
    }
  }, [isMyTurn, phase, subPhase, validTargets, submitAction, state]);

  // SVG viewBox
  const allPixels = ALL_POSITIONS.map(([q, r]) => hexToPixel(q, r));
  const pad = HEX_SIZE + 15;
  const minX = Math.min(...allPixels.map(p => p.x)) - pad;
  const maxX = Math.max(...allPixels.map(p => p.x)) + pad;
  const minY = Math.min(...allPixels.map(p => p.y)) - pad;
  const maxY = Math.max(...allPixels.map(p => p.y)) + pad;
  const viewBox = `${minX} ${minY} ${maxX - minX} ${maxY - minY}`;

  return (
    <svg viewBox={viewBox} style={{ width: "100%", maxHeight: 520, display: "block" }}>
      {/* Grid lines */}
      {GRID_LINES.map((line, i) => (
        <line key={i} x1={line.x1} y1={line.y1} x2={line.x2} y2={line.y2}
          stroke="#333" strokeWidth={1} />
      ))}
      {/* Points */}
      {ALL_POSITIONS.map(([q, r]) => {
        const key = hexKey(q, r);
        const cell = board[key] || null;
        const isTarget = validTargets.has(key);
        const isInRow = pendingRowPositions.has(key);
        const clickable = isMyTurn && (isTarget || (subPhase === "remove_row" && isInRow));

        return (
          <BoardPoint
            key={key}
            q={q} r={r}
            cell={cell}
            isValidTarget={isTarget}
            isSelected={key === selectedRing}
            isInRow={isInRow}
            onClick={clickable ? () => handleClick(key) : null}
          />
        );
      })}
    </svg>
  );
}

// ─── PLAYER PANEL COMPONENT ────────────────────────────────────────

function PlayerPanel({ player, isCurrent, isMe, ringsToWin }) {
  const ps = PIECE_STYLES[player.color];

  return (
    <div style={{
      ...S.card, flex: 1, padding: 12,
      border: `2px solid ${isCurrent ? "#c9a84c" : "#30363d"}`,
      boxShadow: isCurrent ? "0 0 12px rgba(201,168,76,0.3)" : S.card.boxShadow,
    }}>
      <div style={{ display: "flex", alignItems: "center", gap: 8, marginBottom: 6 }}>
        <svg width={20} height={20}>
          <circle cx={10} cy={10} r={8} fill="none" stroke={ps.ring} strokeWidth={3} />
        </svg>
        <span style={{ fontWeight: 700, color: isMe ? "#c9a84c" : "#e8d5a3", fontSize: 14 }}>
          {player.name} {isMe ? "(You)" : ""}
        </span>
      </div>
      <div style={{ fontSize: 12, color: "#888", display: "flex", gap: 16 }}>
        <span>Rings: {player.rings_on_board}</span>
        <span style={{ color: "#c9a84c", fontWeight: 700 }}>
          Removed: {player.rings_removed}/{ringsToWin}
        </span>
      </div>
    </div>
  );
}

// ─── CONFIG PHASE COMPONENT ────────────────────────────────────────

function ConfigPhase({ state, submitAction }) {
  const myIdx = getMyPlayerIdx(state);
  const isHost = myIdx === 0;

  return (
    <div style={{ ...S.card, textAlign: "center" }}>
      <div style={S.cardTitle}>Select Game Mode</div>
      {isHost ? (
        <div style={{ display: "flex", gap: 12, justifyContent: "center" }}>
          <button style={bs(true)} onClick={() => submitAction({ kind: "set_mode", mode: "normal" })}>
            Normal (3 rings)
          </button>
          <button style={bs(false)} onClick={() => submitAction({ kind: "set_mode", mode: "blitz" })}>
            Blitz (1 ring)
          </button>
        </div>
      ) : (
        <div style={{ color: "#888", fontSize: 14 }}>Waiting for host to select mode...</div>
      )}
    </div>
  );
}

// ─── LOBBY COMPONENT ──────────────────────────────────────────────

function Lobby({ game }) {
  const [name, setName] = useState("");
  const [joinCode, setJoinCode] = useState("");
  const [mode, setMode] = useState(null);

  if (!game.roomCode && !mode) {
    return (
      <div style={S.app}>
        <div style={S.overlay} />
        <div style={S.content}>
          <div style={{ textAlign: "center", paddingTop: 60 }}>
            <h1 style={{ ...S.title, fontSize: 48, marginBottom: 8 }}>YINSH</h1>
            <p style={{ color: "#888", fontSize: 14, marginBottom: 32 }}>A game of the GIPF project</p>
            <div style={{ display: "flex", gap: 12, justifyContent: "center" }}>
              <button style={bs(true)} onClick={() => setMode("create")}>Create Room</button>
              <button style={bs(false)} onClick={() => setMode("join")}>Join Room</button>
            </div>
          </div>
        </div>
      </div>
    );
  }

  if (!game.roomCode) {
    return (
      <div style={S.app}>
        <div style={S.overlay} />
        <div style={S.content}>
          <div style={{ textAlign: "center", paddingTop: 60 }}>
            <h1 style={{ ...S.title, marginBottom: 24 }}>YINSH</h1>
            <div style={{ ...S.card, maxWidth: 360, margin: "0 auto" }}>
              <div style={S.cardTitle}>{mode === "create" ? "Create Room" : "Join Room"}</div>
              <div style={{ display: "flex", flexDirection: "column", gap: 12 }}>
                <input
                  style={S.input} placeholder="Your name" value={name}
                  onChange={e => setName(e.target.value)}
                  onKeyDown={e => {
                    if (e.key === "Enter" && name.trim()) {
                      if (mode === "create") game.createRoom(name.trim());
                      else if (joinCode.trim()) game.joinRoom(joinCode.trim(), name.trim());
                    }
                  }}
                />
                {mode === "join" && (
                  <input style={S.input} placeholder="Room code" value={joinCode}
                    onChange={e => setJoinCode(e.target.value.toUpperCase())}
                    onKeyDown={e => {
                      if (e.key === "Enter" && name.trim() && joinCode.trim())
                        game.joinRoom(joinCode.trim(), name.trim());
                    }}
                  />
                )}
                <div style={{ display: "flex", gap: 8 }}>
                  <button style={bs(false)} onClick={() => setMode(null)}>Back</button>
                  <button style={bs(true, !name.trim())} disabled={!name.trim()}
                    onClick={() => {
                      if (mode === "create") game.createRoom(name.trim());
                      else if (joinCode.trim()) game.joinRoom(joinCode.trim(), name.trim());
                    }}
                  >
                    {mode === "create" ? "Create" : "Join"}
                  </button>
                </div>
              </div>
            </div>
            {game.error && <div style={{ color: "#e74c3c", marginTop: 12, fontSize: 13 }}>{game.error}</div>}
          </div>
        </div>
      </div>
    );
  }

  return (
    <div style={S.app}>
      <div style={S.overlay} />
      <div style={S.content}>
        <div style={{ textAlign: "center", paddingTop: 60 }}>
          <h1 style={{ ...S.title, marginBottom: 24 }}>YINSH</h1>
          <div style={{ ...S.card, maxWidth: 400, margin: "0 auto" }}>
            <div style={S.cardTitle}>Room: {game.roomCode}</div>
            <div style={{ marginBottom: 16 }}>
              {game.lobby.map((p, i) => (
                <div key={i} style={{
                  padding: "6px 12px", borderRadius: 6, marginBottom: 4,
                  background: "rgba(255,255,255,0.05)", fontSize: 14,
                  display: "flex", justifyContent: "space-between",
                }}>
                  <span>{p.name}</span>
                  <span style={{ color: "#888", fontSize: 12 }}>{p.is_host ? "Host" : "Player"}</span>
                </div>
              ))}
            </div>
            {game.isHost && game.lobby.length >= 2 && (
              <button style={bs(true)} onClick={() => game.startGame()}>Start Game</button>
            )}
            {game.isHost && game.lobby.length < 2 && (
              <div style={{ color: "#888", fontSize: 13 }}>Waiting for another player...</div>
            )}
            {!game.isHost && (
              <div style={{ color: "#888", fontSize: 13 }}>Waiting for host to start...</div>
            )}
          </div>
          {game.error && <div style={{ color: "#e74c3c", marginTop: 12, fontSize: 13 }}>{game.error}</div>}
        </div>
      </div>
    </div>
  );
}

// ─── GAME BOARD COMPONENT ─────────────────────────────────────────

function GameBoard({ game }) {
  const { gameState: state, phaseInfo, yourTurn, gameLogs, submitAction } = game;
  const logRef = useRef(null);

  // All hooks before null guard
  useEffect(() => {
    if (logRef.current) logRef.current.scrollTop = logRef.current.scrollHeight;
  }, [gameLogs]);

  // Guard — game_state may not have arrived yet
  if (!state || !state.players) {
    return <div style={S.app}><div style={S.content}><div style={S.card}>Loading game...</div></div></div>;
  }

  const myIdx = getMyPlayerIdx(state);
  const oppIdx = 1 - myIdx;
  const me = state.players[myIdx];
  const opp = state.players[oppIdx];
  const myColor = me.color;
  const validActions = state.valid_actions || [];
  const phase = state.phase;
  const subPhase = state.sub_phase;
  const ringsToWin = state.rings_to_win || 3;

  const isMyAction = useMemo(() => {
    if (subPhase === "remove_row" || subPhase === "remove_ring") {
      return state.row_player === myIdx;
    }
    return state.current_player === myIdx;
  }, [state, myIdx, subPhase]);

  const canPass = validActions.some(a => a.kind === "pass");

  // Config phase
  if (phase === "config") {
    return (
      <div style={S.app}>
        <div style={S.overlay} />
        <div style={S.content}>
          <h1 style={{ ...S.title, textAlign: "center", fontSize: 28, marginBottom: 16 }}>YINSH</h1>
          <ConfigPhase state={state} submitAction={submitAction} />
        </div>
      </div>
    );
  }

  // Game over
  if (state.game_over) {
    const winner = state.winner;
    const winnerPlayer = state.players.find(p => p.player_id === winner);
    return (
      <div style={S.app}>
        <div style={S.overlay} />
        <div style={S.content}>
          <h1 style={{ ...S.title, textAlign: "center", fontSize: 28, marginBottom: 16 }}>YINSH — Game Over</h1>
          <div style={{ ...S.card, textAlign: "center" }}>
            <div style={{ fontSize: 22, fontWeight: 700, marginBottom: 12, color: "#c9a84c" }}>
              {winnerPlayer ? `${winnerPlayer.name} wins!` : "It's a draw!"}
            </div>
            <div style={{ display: "flex", gap: 12, justifyContent: "center", marginBottom: 16 }}>
              <PlayerPanel player={me} isCurrent={false} isMe ringsToWin={ringsToWin} />
              <PlayerPanel player={opp} isCurrent={false} isMe={false} ringsToWin={ringsToWin} />
            </div>
          </div>
          <div style={S.card}>
            <HexBoard state={state} submitAction={() => {}} myColor={myColor} />
          </div>
          <div style={S.card}>
            <div style={S.cardTitle}>Game Log</div>
            <div style={{ maxHeight: 200, overflowY: "auto", fontSize: 12, color: "#888" }}>
              {gameLogs.map((msg, i) => <div key={i} style={{ padding: "2px 0" }}>{msg}</div>)}
            </div>
          </div>
        </div>
      </div>
    );
  }

  // Active game
  const phaseLabels = {
    placement: "Ring Placement",
    main: {
      place_marker: "Place a marker",
      move_ring: "Move your ring",
      remove_row: "Remove a row",
      remove_ring: "Remove a ring",
    },
  };
  const phaseLabel = phase === "placement"
    ? "Ring Placement"
    : (phaseLabels.main?.[subPhase] || "Main phase");

  return (
    <div style={S.app}>
      <div style={S.overlay} />
      <div style={S.content}>
        {/* Header */}
        <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 12 }}>
          <h1 style={{ ...S.title, fontSize: 24, margin: 0 }}>YINSH</h1>
          <div style={{ display: "flex", gap: 8, alignItems: "center" }}>
            <div style={{
              padding: "4px 12px", borderRadius: 12, fontSize: 12,
              background: "rgba(255,255,255,0.05)", border: "1px solid #30363d", color: "#888",
            }}>
              {phaseLabel} &middot; {ringsToWin === 1 ? "Blitz" : "Normal"}
            </div>
            <div style={{
              padding: "4px 12px", borderRadius: 12, fontSize: 12,
              background: isMyAction ? "rgba(39,174,96,0.2)" : "rgba(255,255,255,0.05)",
              border: `1px solid ${isMyAction ? "#27ae60" : "#30363d"}`,
              color: isMyAction ? "#27ae60" : "#888",
            }}>
              {phaseInfo?.description || (isMyAction ? "Your turn" : `${opp.name}'s turn`)}
            </div>
          </div>
        </div>

        {/* Player panels */}
        <div style={{ display: "flex", gap: 12, marginBottom: 12 }}>
          <PlayerPanel player={me} isCurrent={state.current_player === myIdx} isMe ringsToWin={ringsToWin} />
          <PlayerPanel player={opp} isCurrent={state.current_player === oppIdx} isMe={false} ringsToWin={ringsToWin} />
        </div>

        {/* Board */}
        <div style={S.card}>
          <HexBoard state={state} submitAction={submitAction} myColor={myColor} />
        </div>

        {/* Action hints */}
        <div style={{ ...S.card, display: "flex", gap: 12, justifyContent: "center", flexWrap: "wrap" }}>
          {isMyAction && phase === "placement" && (
            <div style={{ fontSize: 13, color: "#888" }}>Click an empty intersection to place your ring</div>
          )}
          {isMyAction && subPhase === "place_marker" && !canPass && (
            <div style={{ fontSize: 13, color: "#888" }}>Click one of your rings to place a marker</div>
          )}
          {isMyAction && subPhase === "move_ring" && (
            <div style={{ fontSize: 13, color: "#888" }}>Click a highlighted point to move your ring</div>
          )}
          {isMyAction && subPhase === "remove_row" && (
            <div style={{ fontSize: 13, color: "#c9a84c" }}>Click a highlighted marker to select which row to remove</div>
          )}
          {isMyAction && subPhase === "remove_ring" && (
            <div style={{ fontSize: 13, color: "#c9a84c" }}>Click one of your rings to remove it</div>
          )}
          {canPass && (
            <button style={{ ...S.btn, color: "#e74c3c", borderColor: "#e74c3c" }}
              onClick={() => submitAction({ kind: "pass" })}>
              Pass (No moves)
            </button>
          )}
          {!isMyAction && (
            <div style={{ fontSize: 13, color: "#888" }}>Waiting for {opp.name}...</div>
          )}
        </div>

        {/* Markers remaining */}
        <div style={{ textAlign: "center", fontSize: 12, color: "#555", marginBottom: 8 }}>
          Markers remaining: {state.markers_remaining}
        </div>

        {/* Error */}
        {game.error && (
          <div style={{
            ...S.card, textAlign: "center", padding: 12,
            border: "1px solid #e74c3c", background: "rgba(231,76,60,0.1)",
          }}>
            <span style={{ fontSize: 13, color: "#e74c3c" }}>{game.error}</span>
          </div>
        )}

        {/* Game log */}
        <div style={S.card}>
          <div style={S.cardTitle}>Game Log</div>
          <div ref={logRef} style={{ maxHeight: 150, overflowY: "auto", fontSize: 12, color: "#888" }}>
            {gameLogs.length === 0
              ? <div style={{ padding: "2px 0", fontStyle: "italic" }}>Game started...</div>
              : gameLogs.map((msg, i) => <div key={i} style={{ padding: "2px 0" }}>{msg}</div>)}
          </div>
        </div>
      </div>
    </div>
  );
}

// ─── MAIN APP ──────────────────────────────────────────────────────

export default function App() {
  const game = useGameConnection();

  if (!game.gameStarted) return <Lobby game={game} />;
  return <GameBoard game={game} />;
}
