import React, { useState, useEffect, useRef, useCallback, useMemo } from "react";

// ─── CONFIGURATION ─────────────────────────────────────────────────
import { WS_URL } from "../ws.js";

// ─── CONSTANTS ─────────────────────────────────────────────────────

const ROW_SIZES = [9, 10, 11, 10, 9];
const COL_OFFSETS = [0, 0, 0, 0, 0];

// Half-hex-width indentation per row (symmetric diamond)
const ROW_INDENT = [2, 1, 0, 1, 2];

const PIECE_COLORS = {
  white: { fill: "#e8e0d0", stroke: "#888", text: "#333" },
  black: { fill: "#2c3e50", stroke: "#1a252f", text: "#e8d5a3" },
  dvonn: { fill: "#c0392b", stroke: "#8e2a20", text: "#fff" },
};

const HEX_SIZE = 30;
const SQRT3 = Math.sqrt(3);
const HEX_WIDTH = SQRT3 * HEX_SIZE;
const HALF_HEX_WIDTH = HEX_WIDTH / 2;
const VERT_SPACING = 1.5 * HEX_SIZE;

// ─── HEX MATH ──────────────────────────────────────────────────────

function boardKey(row, col) { return `${row},${col}`; }
function parseKey(key) { const [r, c] = key.split(",").map(Number); return [r, c]; }

// Generate all 49 board positions
function allPositions() {
  const positions = [];
  for (let row = 0; row < ROW_SIZES.length; row++) {
    const offset = COL_OFFSETS[row];
    for (let col = offset; col < offset + ROW_SIZES[row]; col++) {
      positions.push([row, col]);
    }
  }
  return positions;
}

const ALL_POSITIONS = allPositions();

// Pointy-top hex to pixel (diamond layout)
function hexToPixel(row, col) {
  return {
    x: (ROW_INDENT[row] + col * 2) * HALF_HEX_WIDTH,
    y: row * VERT_SPACING,
  };
}

// Pointy-top hex polygon points
function hexPoints(cx, cy) {
  const pts = [];
  for (let i = 0; i < 6; i++) {
    const angle = (Math.PI / 180) * (60 * i - 30);
    pts.push(`${cx + HEX_SIZE * Math.cos(angle)},${cy + HEX_SIZE * Math.sin(angle)}`);
  }
  return pts.join(" ");
}


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
  btnDanger: { background: "linear-gradient(135deg, #c0392b 0%, #962d22 100%)", color: "#fff", border: "1px solid #c0392b" },
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
    connect(() => send({ type: "create", game: "dvonn", name }));
  };
  const joinRoom = (code, name) => {
    connect(() => send({ type: "join", room_code: code.toUpperCase(), name }));
  };
  const startGame = () => send({ type: "start" });
  const submitAction = (action) => send({ type: "action", action });

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

  return {
    connected, roomCode, playerId, token, isHost, lobby,
    gameStarted, gameState, phaseInfo, yourTurn, waitingFor,
    gameLogs, gameOver, error,
    createRoom, joinRoom, startGame, submitAction,
  };
}

// ─── HEX CELL COMPONENT ───────────────────────────────────────────

function HexCell({ row, col, space, isValidDest, isSelected, isLastFrom, isLastTo, isRemoved, onClick }) {
  const { x, y } = hexToPixel(row, col);
  const stack = space?.stack || [];
  const topPiece = stack.length > 0 ? stack[stack.length - 1] : null;
  const hasDvonn = stack.some(p => p === "dvonn");

  let fillColor = "#3a3a3a";
  if (isRemoved) fillColor = "#5a2020";
  else if (isLastTo) fillColor = "#2a3a20";
  else if (isLastFrom) fillColor = "#3a3a20";

  let strokeColor = "#555";
  let strokeWidth = 1.5;
  if (isValidDest) { strokeColor = "#4caf50"; strokeWidth = 3; fillColor = "#2a3a20"; }
  if (isSelected) { strokeColor = "#ffeb3b"; strokeWidth = 3; }

  const pieceColor = topPiece ? PIECE_COLORS[topPiece] : null;

  return (
    <g onClick={onClick} style={{ cursor: onClick ? "pointer" : "default" }}>
      <polygon
        points={hexPoints(x, y)}
        fill={fillColor}
        stroke={strokeColor}
        strokeWidth={strokeWidth}
      />
      {/* Empty space marker */}
      {stack.length === 0 && (
        <circle cx={x} cy={y} r={4} fill="#555" opacity={0.3} />
      )}
      {/* Piece / Stack */}
      {topPiece && (
        <>
          <circle
            cx={x} cy={y} r={HEX_SIZE * 0.55}
            fill={pieceColor.fill}
            stroke={pieceColor.stroke}
            strokeWidth={2}
          />
          {/* Stack height number */}
          {stack.length > 1 && (
            <text
              x={x} y={y + 1}
              textAnchor="middle" dominantBaseline="middle"
              fill={pieceColor.text}
              fontSize={14} fontWeight={700}
              fontFamily="monospace"
            >
              {stack.length}
            </text>
          )}
          {/* Single piece label */}
          {stack.length === 1 && topPiece === "dvonn" && (
            <text
              x={x} y={y + 1}
              textAnchor="middle" dominantBaseline="middle"
              fill={pieceColor.text}
              fontSize={10} fontWeight={700}
              fontFamily="sans-serif"
            >
              D
            </text>
          )}
          {/* DVONN indicator for stacks containing dvonn but not topped by it */}
          {hasDvonn && topPiece !== "dvonn" && (
            <circle
              cx={x + HEX_SIZE * 0.35} cy={y - HEX_SIZE * 0.35}
              r={5} fill="#c0392b" stroke="#8e2a20" strokeWidth={1}
            />
          )}
        </>
      )}
    </g>
  );
}

// ─── HEX BOARD COMPONENT ──────────────────────────────────────────

function HexBoard({ state, selectedStack, setSelectedStack, submitAction, myColor }) {
  const board = state.board;
  const validActions = state.valid_actions || [];
  const phase = state.phase;
  const lastMove = state.last_move;
  const lastRemoved = state.last_removed || [];
  const isMyTurn = state.current_player === getMyPlayerIdx(state);

  // Valid destinations for selected stack
  const validDests = useMemo(() => {
    if (!selectedStack) return new Set();
    return new Set(
      validActions
        .filter(a => a.kind === "move_stack" && a.from === selectedStack)
        .map(a => a.to)
    );
  }, [selectedStack, validActions]);

  // Valid placement positions
  const placementTargets = useMemo(() => {
    if (phase !== "placement") return new Set();
    return new Set(
      validActions
        .filter(a => a.kind === "place_piece")
        .map(a => a.position)
    );
  }, [phase, validActions]);

  // Stacks the player can select (for movement)
  const selectableStacks = useMemo(() => {
    if (phase !== "movement") return new Set();
    return new Set(
      validActions
        .filter(a => a.kind === "move_stack")
        .map(a => a.from)
    );
  }, [phase, validActions]);

  const handleClick = useCallback((key) => {
    if (!isMyTurn) return;

    if (phase === "placement") {
      if (placementTargets.has(key)) {
        submitAction({ kind: "place_piece", position: key });
      }
      return;
    }

    if (phase === "movement") {
      // Click a valid destination
      if (selectedStack && validDests.has(key)) {
        submitAction({ kind: "move_stack", from: selectedStack, to: key });
        setSelectedStack(null);
        return;
      }
      // Click own stack to select
      if (selectableStacks.has(key)) {
        setSelectedStack(key === selectedStack ? null : key);
        return;
      }
      // Click elsewhere to deselect
      setSelectedStack(null);
    }
  }, [isMyTurn, phase, selectedStack, validDests, placementTargets, selectableStacks, submitAction, setSelectedStack]);

  // Compute SVG viewBox
  const allPixels = ALL_POSITIONS.map(([r, c]) => hexToPixel(r, c));
  const minX = Math.min(...allPixels.map(p => p.x)) - HEX_SIZE - 10;
  const maxX = Math.max(...allPixels.map(p => p.x)) + HEX_SIZE + 10;
  const minY = Math.min(...allPixels.map(p => p.y)) - HEX_SIZE - 10;
  const maxY = Math.max(...allPixels.map(p => p.y)) + HEX_SIZE + 10;
  const viewBox = `${minX} ${minY} ${maxX - minX} ${maxY - minY}`;

  const removedSet = new Set(lastRemoved);

  return (
    <svg viewBox={viewBox} style={{ width: "100%", maxHeight: 500, display: "block" }}>
      {ALL_POSITIONS.map(([row, col]) => {
        const key = boardKey(row, col);
        const space = board[key];
        const isValidDest = validDests.has(key) || (phase === "placement" && placementTargets.has(key));
        const isSelected = key === selectedStack;
        const isSelectableStack = selectableStacks.has(key);
        const isLastFrom = lastMove?.from === key;
        const isLastTo = lastMove?.to === key;
        const isRemoved = removedSet.has(key);
        const clickable = isMyTurn && (isValidDest || isSelectableStack || isSelected);

        return (
          <HexCell
            key={key}
            row={row} col={col}
            space={space}
            isValidDest={isValidDest}
            isSelected={isSelected}
            isLastFrom={isLastFrom}
            isLastTo={isLastTo}
            isRemoved={isRemoved}
            onClick={clickable ? () => handleClick(key) : null}
          />
        );
      })}
    </svg>
  );
}

// ─── PLAYER PANEL COMPONENT ────────────────────────────────────────

function PlayerPanel({ player, isCurrent, isMe, state }) {
  const phase = state.phase;
  const board = state.board;

  // Count pieces controlled in movement phase
  const controlled = useMemo(() => {
    if (phase !== "movement" && phase !== "game_over") return 0;
    let total = 0;
    for (const space of Object.values(board)) {
      if (space.stack.length > 0 && space.stack[space.stack.length - 1] === player.color) {
        total += space.stack.length;
      }
    }
    return total;
  }, [board, player.color, phase]);

  const borderColor = isCurrent ? "#c9a84c" : "#30363d";
  const pColor = PIECE_COLORS[player.color];

  return (
    <div style={{
      ...S.card, flex: 1, padding: 12,
      border: `2px solid ${borderColor}`,
      boxShadow: isCurrent ? "0 0 12px rgba(201,168,76,0.3)" : S.card.boxShadow,
    }}>
      <div style={{ display: "flex", alignItems: "center", gap: 8, marginBottom: 6 }}>
        <div style={{
          width: 18, height: 18, borderRadius: "50%",
          background: pColor.fill, border: `2px solid ${pColor.stroke}`,
        }} />
        <span style={{ fontWeight: 700, color: isMe ? "#c9a84c" : "#e8d5a3", fontSize: 14 }}>
          {player.name} {isMe ? "(You)" : ""}
        </span>
      </div>
      {phase === "placement" && (
        <div style={{ fontSize: 12, color: "#888" }}>
          {player.dvonn_to_place > 0
            ? `DVONN pieces: ${player.dvonn_to_place}`
            : `Pieces to place: ${player.pieces_to_place}`}
        </div>
      )}
      {(phase === "movement" || phase === "game_over") && (
        <div style={{ fontSize: 12, color: "#888" }}>
          Controls: {controlled} pieces
        </div>
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
            <h1 style={{ ...S.title, fontSize: 48, marginBottom: 8 }}>DVONN</h1>
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
            <h1 style={{ ...S.title, marginBottom: 24 }}>DVONN</h1>
            <div style={{ ...S.card, maxWidth: 360, margin: "0 auto" }}>
              <div style={S.cardTitle}>{mode === "create" ? "Create Room" : "Join Room"}</div>
              <div style={{ display: "flex", flexDirection: "column", gap: 12 }}>
                <input
                  style={S.input}
                  placeholder="Your name"
                  value={name}
                  onChange={e => setName(e.target.value)}
                  onKeyDown={e => {
                    if (e.key === "Enter" && name.trim()) {
                      if (mode === "create") game.createRoom(name.trim());
                      else if (joinCode.trim()) game.joinRoom(joinCode.trim(), name.trim());
                    }
                  }}
                />
                {mode === "join" && (
                  <input
                    style={S.input}
                    placeholder="Room code"
                    value={joinCode}
                    onChange={e => setJoinCode(e.target.value.toUpperCase())}
                    onKeyDown={e => {
                      if (e.key === "Enter" && name.trim() && joinCode.trim())
                        game.joinRoom(joinCode.trim(), name.trim());
                    }}
                  />
                )}
                <div style={{ display: "flex", gap: 8 }}>
                  <button style={bs(false)} onClick={() => setMode(null)}>Back</button>
                  <button
                    style={bs(true, !name.trim())}
                    disabled={!name.trim()}
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
            {game.error && (
              <div style={{ color: "#e74c3c", marginTop: 12, fontSize: 13 }}>{game.error}</div>
            )}
          </div>
        </div>
      </div>
    );
  }

  // In lobby with room code
  return (
    <div style={S.app}>
      <div style={S.overlay} />
      <div style={S.content}>
        <div style={{ textAlign: "center", paddingTop: 60 }}>
          <h1 style={{ ...S.title, marginBottom: 24 }}>DVONN</h1>
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
          {game.error && (
            <div style={{ color: "#e74c3c", marginTop: 12, fontSize: 13 }}>{game.error}</div>
          )}
        </div>
      </div>
    </div>
  );
}

// ─── GAME BOARD COMPONENT ─────────────────────────────────────────

function GameBoard({ game }) {
  const { gameState: state, phaseInfo, yourTurn, gameLogs, submitAction } = game;
  const [selectedStack, setSelectedStack] = useState(null);
  const logRef = useRef(null);

  const phase = state?.phase;

  // All hooks must be called before any early return (React rules of hooks)
  useEffect(() => { setSelectedStack(null); }, [state?.current_player, phase]);
  useEffect(() => {
    if (logRef.current) logRef.current.scrollTop = logRef.current.scrollHeight;
  }, [gameLogs]);

  // Guard — game_state message may not have arrived yet (race with game_started)
  if (!state || !state.players) {
    return <div style={S.app}><div style={S.content}><div style={S.card}>Loading game...</div></div></div>;
  }

  const myIdx = getMyPlayerIdx(state);
  const oppIdx = 1 - myIdx;
  const me = state.players[myIdx];
  const opp = state.players[oppIdx];
  const myColor = me.color;
  const validActions = state.valid_actions || [];
  const isCurrent = state.current_player === myIdx;

  const canPass = validActions.some(a => a.kind === "pass");

  // Game over
  if (state.game_over) {
    const winner = state.winner;
    const winnerPlayer = state.players.find(p => p.player_id === winner);
    return (
      <div style={S.app}>
        <div style={S.overlay} />
        <div style={S.content}>
          <h1 style={{ ...S.title, textAlign: "center", fontSize: 28, marginBottom: 16 }}>DVONN — Game Over</h1>
          <div style={{ ...S.card, textAlign: "center" }}>
            <div style={{ fontSize: 22, fontWeight: 700, marginBottom: 12, color: "#c9a84c" }}>
              {winnerPlayer ? `${winnerPlayer.name} wins!` : "It's a draw!"}
            </div>
            <div style={{ display: "flex", gap: 12, justifyContent: "center", marginBottom: 16 }}>
              <PlayerPanel player={me} isCurrent={false} isMe state={state} />
              <PlayerPanel player={opp} isCurrent={false} isMe={false} state={state} />
            </div>
          </div>
          <div style={S.card}>
            <HexBoard
              state={state}
              selectedStack={null}
              setSelectedStack={() => {}}
              submitAction={() => {}}
              myColor={myColor}
            />
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
  const phaseLabel = phase === "placement"
    ? (state.placement_sub_phase === "dvonn" ? "Place DVONN pieces" : "Place colored pieces")
    : "Movement phase";

  return (
    <div style={S.app}>
      <div style={S.overlay} />
      <div style={S.content}>
        {/* Header */}
        <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 12 }}>
          <h1 style={{ ...S.title, fontSize: 24, margin: 0 }}>DVONN</h1>
          <div style={{ display: "flex", gap: 8, alignItems: "center" }}>
            <div style={{
              padding: "4px 12px", borderRadius: 12, fontSize: 12,
              background: "rgba(255,255,255,0.05)", border: "1px solid #30363d", color: "#888",
            }}>
              {phaseLabel} &middot; {state.pieces_placed}/49
            </div>
            <div style={{
              padding: "4px 12px", borderRadius: 12, fontSize: 12,
              background: isCurrent ? "rgba(39,174,96,0.2)" : "rgba(255,255,255,0.05)",
              border: `1px solid ${isCurrent ? "#27ae60" : "#30363d"}`,
              color: isCurrent ? "#27ae60" : "#888",
            }}>
              {isCurrent ? phaseInfo?.description || "Your turn" : `${opp.name}'s turn`}
            </div>
          </div>
        </div>

        {/* Player panels */}
        <div style={{ display: "flex", gap: 12, marginBottom: 12 }}>
          <PlayerPanel player={me} isCurrent={state.current_player === myIdx} isMe state={state} />
          <PlayerPanel player={opp} isCurrent={state.current_player === oppIdx} isMe={false} state={state} />
        </div>

        {/* Board */}
        <div style={S.card}>
          <HexBoard
            state={state}
            selectedStack={selectedStack}
            setSelectedStack={setSelectedStack}
            submitAction={submitAction}
            myColor={myColor}
          />
        </div>

        {/* Action buttons */}
        <div style={{ ...S.card, display: "flex", gap: 12, justifyContent: "center", flexWrap: "wrap" }}>
          {phase === "placement" && isCurrent && (
            <div style={{ fontSize: 13, color: "#888", padding: "4px 0" }}>
              Click an empty space to place your piece
            </div>
          )}
          {phase === "movement" && isCurrent && !canPass && (
            <div style={{ fontSize: 13, color: "#888", padding: "4px 0" }}>
              {selectedStack
                ? "Click a highlighted space to move there, or click another stack"
                : "Click one of your stacks to select it"}
            </div>
          )}
          {canPass && (
            <button
              style={{ ...S.btn, color: "#e74c3c", borderColor: "#e74c3c" }}
              onClick={() => submitAction({ kind: "pass" })}
            >
              Pass (No moves)
            </button>
          )}
          {!isCurrent && (
            <div style={{ fontSize: 13, color: "#888", padding: "4px 0" }}>
              Waiting for {opp.name}...
            </div>
          )}
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
