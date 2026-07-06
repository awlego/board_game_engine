import React, { useState, useEffect, useRef, useCallback, useMemo } from "react";

// ─── CONFIGURATION ─────────────────────────────────────────────────
import { WS_URL } from "../ws.js";

// ─── CONSTANTS ─────────────────────────────────────────────────────

const BOARD_RADIUS = 3;

// Axial hex direction vectors
const HEX_DIRS = [[1,0],[-1,0],[0,1],[0,-1],[1,-1],[-1,1]];

// Corner positions matching server order
const CORNERS = [[3,0],[0,3],[-3,3],[-3,0],[0,-3],[3,-3]];

const COLORS = {
  black: { main: "#2c3e50", light: "#4a6a82", ring: "#1a252f" },
  red:   { main: "#c0392b", light: "#e06050", ring: "#8e2a20" },
};

const HEX_SIZE = 42;
const SQRT3 = Math.sqrt(3);

// ─── HEX MATH ──────────────────────────────────────────────────────

function hexKey(q, r) { return `${q},${r}`; }
function parseHex(key) { const [q, r] = key.split(",").map(Number); return [q, r]; }

function hexDistance(q1, r1, q2, r2) {
  const dq = q1 - q2, dr = r1 - r2;
  return Math.max(Math.abs(dq), Math.abs(dr), Math.abs(dq + dr));
}

function isValid(q, r) { return hexDistance(0, 0, q, r) <= BOARD_RADIUS; }

// Flat-top hex: axial to pixel
function hexToPixel(q, r) {
  return {
    x: HEX_SIZE * 1.5 * q,
    y: HEX_SIZE * (SQRT3 / 2 * q + SQRT3 * r),
  };
}

// Hex polygon points (flat-top)
function hexPoints(cx, cy) {
  const pts = [];
  for (let i = 0; i < 6; i++) {
    const angle = (Math.PI / 180) * (60 * i);
    pts.push(`${cx + HEX_SIZE * Math.cos(angle)},${cy + HEX_SIZE * Math.sin(angle)}`);
  }
  return pts.join(" ");
}

// Generate all 37 board positions
function allPositions() {
  const positions = [];
  for (let q = -BOARD_RADIUS; q <= BOARD_RADIUS; q++) {
    for (let r = -BOARD_RADIUS; r <= BOARD_RADIUS; r++) {
      if (isValid(q, r)) positions.push([q, r]);
    }
  }
  return positions;
}

function ringCapacity(q, r) {
  const d = hexDistance(0, 0, q, r);
  return d === 0 ? 4 : d === 1 ? 3 : d === 2 ? 2 : 1;
}

const ALL_POSITIONS = allPositions();

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

function formatTime(seconds) {
  if (seconds <= 0) return "0:00";
  const m = Math.floor(seconds / 60);
  const s = Math.floor(seconds % 60);
  return `${m}:${s.toString().padStart(2, "0")}`;
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
    connect(() => send({ type: "create", game: "tamsk", name }));
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

// ─── HOURGLASS TIMER HOOK ──────────────────────────────────────────

function useTimers(hourglasses, level) {
  const [now, setNow] = useState(Date.now() / 1000);

  useEffect(() => {
    if (level < 2 || !hourglasses) return;
    const id = setInterval(() => setNow(Date.now() / 1000), 200);
    return () => clearInterval(id);
  }, [level, hourglasses]);

  const getRemaining = useCallback((h) => {
    if (!h || h.is_dead) return 0;
    if (!h.timer_started_at) return h.timer_remaining;
    const elapsed = now - h.timer_started_at;
    return Math.max(0, h.timer_remaining - elapsed);
  }, [now]);

  return { getRemaining };
}

// ─── HEX CAPACITY COLORS ──────────────────────────────────────────
// 5 shades: 0 remaining = nearly invisible (matches bg), 4 = brightest
const PAGE_BG = "#0d1117";
const HEX_CAPACITY_FILLS = [
  "rgba(13,17,23,0.85)",  // 0 remaining — fades into background
  "#1e2328",              // 1 remaining
  "#2a2518",              // 2 remaining
  "#3d3420",              // 3 remaining
  "#5a4a2a",              // 4 remaining
];
const HEX_CAPACITY_STROKES = [
  "#1e2328",   // 0 — barely visible border
  "#30363d",   // 1
  "#4a3d22",   // 2
  "#6b5a3a",   // 3
  "#8b7a4a",   // 4
];

// ─── HEX CELL COMPONENT ───────────────────────────────────────────

function HexCell({ q, r, space, hourglass, isValidDest, isSelected, isRingWindow, canPlaceRing: cellCanPlace, ringWindowStart, isHovered, onClick, onHover, level, getRemaining, myColor, showRings }) {
  const { x, y } = hexToPixel(q, r);
  const cap = space?.capacity || ringCapacity(q, r);
  const rings = space?.rings || [];
  const slotsLeft = cap - rings.length;

  // Color based on remaining capacity
  let fillColor = HEX_CAPACITY_FILLS[Math.min(slotsLeft, 4)];
  let strokeColor = HEX_CAPACITY_STROKES[Math.min(slotsLeft, 4)];
  let strokeWidth = 1.5;

  if (isValidDest) {
    strokeColor = "#4caf50";
    strokeWidth = 3;
    fillColor = "#2a3a20";
  }
  if (isHovered) {
    fillColor = "#3a4a28";
    strokeColor = "#6cdf80";
    strokeWidth = 3;
  }
  if (isSelected) {
    strokeColor = "#ffeb3b";
    strokeWidth = 3;
  }
  if (isRingWindow && cellCanPlace) {
    strokeColor = "#c9a84c";
    strokeWidth = 3;
  }

  const remaining = hourglass ? getRemaining(hourglass) : 0;
  const timerFrac = hourglass && !hourglass.is_dead ? remaining / 180 : 0;

  return (
    <g
      onClick={onClick}
      onMouseEnter={onHover ? () => onHover(true) : undefined}
      onMouseLeave={onHover ? () => onHover(false) : undefined}
      style={{ cursor: onClick ? "pointer" : "default" }}
    >
      {/* Hex background */}
      <polygon
        points={hexPoints(x, y)}
        fill={fillColor}
        stroke={strokeColor}
        strokeWidth={strokeWidth}
      />

      {/* Ring window animation — fading ring hint */}
      {isRingWindow && cellCanPlace && (
        <circle
          cx={x}
          cy={y}
          r={HEX_SIZE * 0.35}
          fill="none"
          stroke="#c9a84c"
          strokeWidth={3}
          style={{ animation: "ring-fade 3s ease-out forwards" }}
        />
      )}

      {/* Ring indicators — only when toggle is on */}
      {showRings && rings.map((color, i) => {
        const ringY = y + HEX_SIZE * 0.42 - i * 5;
        return (
          <circle
            key={i}
            cx={x}
            cy={ringY}
            r={HEX_SIZE * 0.28 - i * 2}
            fill="none"
            stroke={COLORS[color]?.main || "#888"}
            strokeWidth={3}
            opacity={0.9}
          />
        );
      })}

      {/* Ring count — show when rings exist (regardless of toggle) */}
      {rings.length > 0 && (
        <text
          x={x + HEX_SIZE * 0.6}
          y={y + HEX_SIZE * 0.7}
          fill="#aaa"
          fontSize={9}
          textAnchor="middle"
          fontFamily={font}
        >
          {rings.length}/{cap}
        </text>
      )}

      {/* Hourglass */}
      {hourglass && (
        <g>
          {/* Hourglass body — two triangles */}
          <polygon
            points={`${x},${y - 18} ${x - 10},${y - 4} ${x + 10},${y - 4}`}
            fill={hourglass.is_dead ? "#555" : COLORS[hourglass.color]?.main}
            stroke={hourglass.is_dead ? "#777" : COLORS[hourglass.color]?.light}
            strokeWidth={1.5}
            opacity={hourglass.is_dead ? 0.5 : 1}
          />
          <polygon
            points={`${x},${y + 8} ${x - 10},${y - 6} ${x + 10},${y - 6}`}
            fill={hourglass.is_dead ? "#555" : COLORS[hourglass.color]?.main}
            stroke={hourglass.is_dead ? "#777" : COLORS[hourglass.color]?.light}
            strokeWidth={1.5}
            opacity={hourglass.is_dead ? 0.5 : 1}
          />

          {/* Dead X */}
          {hourglass.is_dead && (
            <text
              x={x}
              y={y + 2}
              fill="#e74c3c"
              fontSize={22}
              textAnchor="middle"
              dominantBaseline="middle"
              fontWeight="bold"
            >
              X
            </text>
          )}

          {/* Timer text (Level 2/3) */}
          {level >= 2 && !hourglass.is_dead && (
            <text
              x={x}
              y={y + 22}
              fill={timerFrac > 0.5 ? "#4caf50" : timerFrac > 0.2 ? "#ff9800" : "#e74c3c"}
              fontSize={10}
              textAnchor="middle"
              fontFamily="monospace"
              fontWeight="bold"
            >
              {formatTime(remaining)}
            </text>
          )}

          {/* Hourglass label */}
          <text
            x={x}
            y={y - 22}
            fill={hourglass.is_dead ? "#777" : COLORS[hourglass.color]?.light}
            fontSize={8}
            textAnchor="middle"
            fontFamily={font}
            fontWeight="bold"
          >
            {hourglass.id.replace("_", " ")}
          </text>
        </g>
      )}
    </g>
  );
}

// ─── HEX BOARD ─────────────────────────────────────────────────────

function HexBoard({ state, selectedHourglass, setSelectedHourglass, submitAction, myColor, level, getRemaining, bonusRingSpaces, showRings, ringWindowSpace, ringWindowStart, canPlaceRing, hoveredHex, setHoveredHex }) {
  const board = state.board;
  const hourglasses = state.hourglasses || {};
  const validActions = state.valid_actions || [];
  const isBonusPhase = bonusRingSpaces && bonusRingSpaces.size > 0;

  // Build position → hourglass lookup
  const posToHourglass = useMemo(() => {
    const map = {};
    for (const h of Object.values(hourglasses)) {
      if (h.position) map[h.position] = h;
    }
    return map;
  }, [hourglasses]);

  // Valid destinations for selected hourglass
  const validDests = useMemo(() => {
    if (!selectedHourglass) return new Set();
    return new Set(
      validActions
        .filter(a => a.kind === "move_hourglass" && a.hourglass_id === selectedHourglass)
        .map(a => a.to)
    );
  }, [selectedHourglass, validActions]);

  // Which hourglasses can be moved?
  const movableHourglasses = useMemo(() => {
    return new Set(
      validActions
        .filter(a => a.kind === "move_hourglass")
        .map(a => a.hourglass_id)
    );
  }, [validActions]);

  // Compute SVG bounds
  const padding = 40;
  const positions = ALL_POSITIONS.map(([q, r]) => hexToPixel(q, r));
  const minX = Math.min(...positions.map(p => p.x)) - HEX_SIZE - padding;
  const maxX = Math.max(...positions.map(p => p.x)) + HEX_SIZE + padding;
  const minY = Math.min(...positions.map(p => p.y)) - HEX_SIZE - padding;
  const maxY = Math.max(...positions.map(p => p.y)) + HEX_SIZE + padding;

  const handleCellClick = (q, r) => {
    const key = hexKey(q, r);
    const hg = posToHourglass[key];

    // Ring window — click the destination hourglass to place a ring
    if (state.sub_phase === "ring_window" && key === ringWindowSpace && canPlaceRing) {
      submitAction({ kind: "place_ring" });
      return;
    }

    // Bonus ring placement — click any highlighted space
    if (isBonusPhase && bonusRingSpaces.has(key)) {
      submitAction({ kind: "place_bonus_ring", space: key });
      return;
    }

    // If clicking a valid destination, move there
    if (selectedHourglass && validDests.has(key)) {
      submitAction({ kind: "move_hourglass", hourglass_id: selectedHourglass, to: key });
      setSelectedHourglass(null);
      return;
    }

    // If clicking one of my movable hourglasses, select it
    if (hg && hg.color === myColor && movableHourglasses.has(hg.id)) {
      setSelectedHourglass(selectedHourglass === hg.id ? null : hg.id);
      return;
    }

    // Deselect
    setSelectedHourglass(null);
  };

  return (
    <svg
      viewBox={`${minX} ${minY} ${maxX - minX} ${maxY - minY}`}
      style={{ width: "100%", maxHeight: 520, display: "block" }}
    >
      {ALL_POSITIONS.map(([q, r]) => {
        const key = hexKey(q, r);
        const hg = posToHourglass[key];
        const isBonusDest = isBonusPhase && bonusRingSpaces.has(key);
        const isRingWindow = state.sub_phase === "ring_window" && key === ringWindowSpace;
        const isHovered = hoveredHex === key && isBonusDest;
        return (
          <HexCell
            key={key}
            q={q}
            r={r}
            space={board[key]}
            hourglass={hg}
            isValidDest={validDests.has(key) || isBonusDest}
            isSelected={hg && selectedHourglass === hg.id}
            isRingWindow={isRingWindow}
            canPlaceRing={isRingWindow && canPlaceRing}
            ringWindowStart={isRingWindow ? ringWindowStart : null}
            isHovered={isHovered}
            onClick={() => handleCellClick(q, r)}
            onHover={setHoveredHex ? (entering) => setHoveredHex(entering ? key : null) : undefined}
            level={level}
            getRemaining={getRemaining}
            myColor={myColor}
            showRings={showRings}
          />
        );
      })}
    </svg>
  );
}

// ─── PLAYER PANEL ──────────────────────────────────────────────────

function PlayerPanel({ player, isCurrent, isMe, hourglasses, level, getRemaining }) {
  const color = COLORS[player.color];
  const placed = 32 - player.rings_remaining;
  const myHourglasses = Object.values(hourglasses || {}).filter(h => h.color === player.color);

  return (
    <div style={{
      padding: "12px 16px", borderRadius: 8,
      background: isCurrent ? `${color.main}22` : "rgba(0,0,0,0.2)",
      border: `2px solid ${isCurrent ? color.main : "#30363d"}`,
      flex: 1,
    }}>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 8 }}>
        <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
          <div style={{
            width: 14, height: 14, borderRadius: "50%",
            background: color.main, border: `2px solid ${color.light}`,
          }} />
          <span style={{ fontSize: 16, fontWeight: 700, color: color.light }}>
            {player.name} {isMe ? "(you)" : ""}
          </span>
        </div>
        {isCurrent && (
          <span style={{
            padding: "2px 10px", borderRadius: 10, fontSize: 11,
            background: `${color.main}44`, color: color.light,
          }}>
            Current Turn
          </span>
        )}
      </div>
      <div style={{ display: "flex", gap: 16, fontSize: 13, color: "#bbb" }}>
        <div>Rings placed: <strong style={{ color: color.light }}>{placed}</strong></div>
        <div>Remaining: <strong style={{ color: "#e8d5a3" }}>{player.rings_remaining}</strong></div>
      </div>
      {level >= 2 && (
        <div style={{ display: "flex", gap: 8, marginTop: 8 }}>
          {myHourglasses.map(h => (
            <div key={h.id} style={{
              padding: "2px 8px", borderRadius: 4, fontSize: 10,
              background: h.is_dead ? "rgba(231,76,60,0.2)" : "rgba(39,174,96,0.15)",
              border: `1px solid ${h.is_dead ? "#e74c3c44" : "#27ae6044"}`,
              color: h.is_dead ? "#e74c3c" : "#27ae60",
            }}>
              {h.id.split("_")[1]}: {h.is_dead ? "DEAD" : formatTime(getRemaining(h))}
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

// ─── LOBBY ─────────────────────────────────────────────────────────

function Lobby({ game }) {
  const [name, setName] = useState("");
  const [joinCode, setJoinCode] = useState("");
  const [mode, setMode] = useState(null);

  if (game.roomCode) {
    return (
      <div style={S.app}>
        <div style={S.overlay} />
        <div style={S.content}>
          <h1 style={{ ...S.title, textAlign: "center", marginBottom: 24 }}>TAMSK</h1>
          <div style={S.card}>
            <div style={S.cardTitle}>
              Room: <span style={{ letterSpacing: 3, fontSize: 22 }}>{game.roomCode}</span>
            </div>
            <div style={{ marginBottom: 16 }}>
              <div style={{ color: "#999", fontSize: 13, marginBottom: 8 }}>Players:</div>
              {game.lobby.map((p) => (
                <div key={p.player_id} style={{
                  padding: "6px 12px", marginBottom: 4, borderRadius: 4,
                  background: p.connected ? "rgba(39,174,96,0.15)" : "rgba(231,76,60,0.15)",
                  border: `1px solid ${p.connected ? "#27ae6044" : "#e74c3c44"}`,
                  fontSize: 14,
                }}>
                  {p.name} {p.is_host ? "(host)" : ""} {!p.connected ? "(disconnected)" : ""}
                </div>
              ))}
            </div>
            {game.isHost && game.lobby.length >= 2 && (
              <button style={bs(true)} onClick={game.startGame}>
                Start Game
              </button>
            )}
            {game.isHost && game.lobby.length < 2 && (
              <div style={{ color: "#888", fontSize: 13 }}>
                Waiting for another player to join...
              </div>
            )}
            {!game.isHost && (
              <div style={{ color: "#888", fontSize: 13 }}>
                Waiting for host to start...
              </div>
            )}
          </div>
          {game.error && (
            <div style={{ color: "#e74c3c", padding: 12, background: "rgba(231,76,60,0.1)", borderRadius: 6 }}>
              {game.error}
            </div>
          )}
        </div>
      </div>
    );
  }

  return (
    <div style={S.app}>
      <div style={S.overlay} />
      <div style={S.content}>
        <h1 style={{ ...S.title, textAlign: "center", marginBottom: 8 }}>TAMSK</h1>
        <p style={{ textAlign: "center", color: "#999", marginBottom: 32, fontSize: 14 }}>
          A territorial game with time as a special feature
        </p>
        {!mode && (
          <div style={{ ...S.card, display: "flex", gap: 12, justifyContent: "center" }}>
            <button style={bs(true)} onClick={() => setMode("create")}>Create Room</button>
            <button style={bs(false)} onClick={() => setMode("join")}>Join Room</button>
          </div>
        )}
        {mode === "create" && (
          <div style={S.card}>
            <div style={S.cardTitle}>Create a Room</div>
            <div style={{ display: "flex", gap: 8 }}>
              <input style={S.input} placeholder="Your name" value={name}
                onChange={(e) => setName(e.target.value)}
                onKeyDown={(e) => e.key === "Enter" && name.trim() && game.createRoom(name.trim())} />
              <button style={bs(true, !name.trim())} disabled={!name.trim()}
                onClick={() => game.createRoom(name.trim())}>Create</button>
            </div>
          </div>
        )}
        {mode === "join" && (
          <div style={S.card}>
            <div style={S.cardTitle}>Join a Room</div>
            <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
              <input style={{ ...S.input, maxWidth: 140 }} placeholder="Room code"
                value={joinCode} onChange={(e) => setJoinCode(e.target.value.toUpperCase())} />
              <input style={S.input} placeholder="Your name" value={name}
                onChange={(e) => setName(e.target.value)}
                onKeyDown={(e) => e.key === "Enter" && name.trim() && joinCode.trim() && game.joinRoom(joinCode.trim(), name.trim())} />
              <button style={bs(true, !name.trim() || !joinCode.trim())}
                disabled={!name.trim() || !joinCode.trim()}
                onClick={() => game.joinRoom(joinCode.trim(), name.trim())}>Join</button>
            </div>
          </div>
        )}
        {mode && (
          <button style={{ ...S.btn, marginTop: 8 }} onClick={() => setMode(null)}>Back</button>
        )}
        {game.error && (
          <div style={{ color: "#e74c3c", padding: 12, marginTop: 12, background: "rgba(231,76,60,0.1)", borderRadius: 6 }}>
            {game.error}
          </div>
        )}
      </div>
    </div>
  );
}

// ─── CONFIG PHASE ──────────────────────────────────────────────────

function ConfigPhase({ state, submitAction }) {
  const validActions = state.valid_actions || [];
  const canSetLevel = validActions.some(a => a.kind === "set_level");

  return (
    <div style={{ ...S.card, textAlign: "center" }}>
      <div style={S.cardTitle}>Choose Difficulty Level</div>
      {canSetLevel ? (
        <div style={{ display: "flex", gap: 12, justifyContent: "center", flexWrap: "wrap" }}>
          <button
            style={{ ...S.btn, ...S.btnP, padding: "16px 24px", minWidth: 160 }}
            onClick={() => submitAction({ kind: "set_level", level: 1 })}
          >
            <div style={{ fontSize: 18, marginBottom: 4 }}>Level 1</div>
            <div style={{ fontSize: 11, fontWeight: 400, opacity: 0.8 }}>No Timers</div>
          </button>
          <button
            style={{ ...S.btn, padding: "16px 24px", minWidth: 160, border: "1px solid #ff9800", color: "#ff9800" }}
            onClick={() => submitAction({ kind: "set_level", level: 2 })}
          >
            <div style={{ fontSize: 18, marginBottom: 4 }}>Level 2</div>
            <div style={{ fontSize: 11, fontWeight: 400, opacity: 0.8 }}>Timers Active</div>
          </button>
          <button
            style={{ ...S.btn, ...S.btnDanger, padding: "16px 24px", minWidth: 160 }}
            onClick={() => submitAction({ kind: "set_level", level: 3 })}
          >
            <div style={{ fontSize: 18, marginBottom: 4 }}>Level 3</div>
            <div style={{ fontSize: 11, fontWeight: 400, opacity: 0.8 }}>Full Game</div>
          </button>
        </div>
      ) : (
        <div style={{ color: "#888", fontSize: 14 }}>Waiting for host to select level...</div>
      )}
    </div>
  );
}

// ─── GAME BOARD ────────────────────────────────────────────────────

function GameBoard({ game }) {
  const { gameState: state, phaseInfo, yourTurn, submitAction, gameLogs, gameOver } = game;
  const [selectedHourglass, setSelectedHourglass] = useState(null);
  const [showRings, setShowRings] = useState(false);
  const logRef = useRef(null);

  const myIdx = useMemo(() => getMyPlayerIdx(state), [state]);
  const oppIdx = 1 - myIdx;
  const me = state.players[myIdx];
  const opp = state.players[oppIdx];
  const level = state.level || 1;
  const myColor = me.color;

  const { getRemaining } = useTimers(state.hourglasses, level);

  const validActions = state.valid_actions || [];
  const subPhase = state.sub_phase;
  const isCurrent = state.current_player === myIdx;

  // Auto-pass notification
  const [autoPassNotice, setAutoPassNotice] = useState(null);

  useEffect(() => {
    if (state.auto_passed) {
      const passedPlayer = state.players.find(p => p.player_id === state.auto_passed);
      const isMe = state.auto_passed === state.your_player_id;
      setAutoPassNotice(
        isMe
          ? "You were auto-passed (no valid moves)"
          : `${passedPlayer?.name || "Opponent"} was auto-passed (no valid moves)`
      );
      const timer = setTimeout(() => setAutoPassNotice(null), 4000);
      return () => clearTimeout(timer);
    }
  }, [state.auto_passed, state.turn_number]);

  // Ring window (timed click to place ring)
  const canPlaceRing = validActions.some(a => a.kind === "place_ring");
  const ringWindowSpace = state.ring_window_space;
  const ringWindowStart = state.ring_window_start;
  const ringWindowMover = state.ring_window_mover;

  // Bonus ring (pressure penalty)
  const canBonusRing = validActions.some(a => a.kind === "place_bonus_ring");
  const canSkipBonusRing = validActions.some(a => a.kind === "skip_bonus_ring");
  const bonusRingSpaces = useMemo(() => {
    return new Set(validActions.filter(a => a.kind === "place_bonus_ring").map(a => a.space));
  }, [validActions]);
  // Can activate pressure?
  const canPressure = validActions.some(a => a.kind === "activate_pressure");

  // Bonus ring popup
  const showBonusPopup = subPhase === "bonus_ring" && isCurrent && canBonusRing;

  // Hover state for bonus ring hex highlighting
  const [hoveredHex, setHoveredHex] = useState(null);

  // Turn banner — strong notification when it becomes your turn
  const [showTurnBanner, setShowTurnBanner] = useState(false);
  const prevYourTurn = useRef(false);
  useEffect(() => {
    if (yourTurn && !prevYourTurn.current && state.phase === "play") {
      setShowTurnBanner(true);
      const t = setTimeout(() => setShowTurnBanner(false), 2000);
      return () => clearTimeout(t);
    }
    prevYourTurn.current = yourTurn;
  }, [yourTurn, state.phase]);

  // Deselect hourglass if its timer hits zero
  useEffect(() => {
    if (!selectedHourglass || !state.hourglasses) return;
    const h = state.hourglasses[selectedHourglass];
    if (h && getRemaining(h) <= 0) setSelectedHourglass(null);
  }, [selectedHourglass, state.hourglasses, getRemaining]);

  // Pressure timer state — persistent hourglass, always shown in Level 3
  const pressureTimer = state.pressure_timer;
  const [pressureNow, setPressureNow] = useState(Date.now() / 1000);

  useEffect(() => {
    if (level < 3 || !pressureTimer?.timer_started_at) return;
    const id = setInterval(() => setPressureNow(Date.now() / 1000), 200);
    return () => clearInterval(id);
  }, [level, pressureTimer?.timer_started_at]);

  const pressureRemaining = useMemo(() => {
    if (!pressureTimer) return 15;
    if (!pressureTimer.timer_started_at) return pressureTimer.timer_remaining;
    const elapsed = pressureNow - pressureTimer.timer_started_at;
    return Math.max(0, pressureTimer.timer_remaining - elapsed);
  }, [pressureTimer, pressureNow]);

  // Clear selection on phase changes
  useEffect(() => { setSelectedHourglass(null); }, [subPhase, state.current_player]);
  useEffect(() => {
    if (logRef.current) logRef.current.scrollTop = logRef.current.scrollHeight;
  }, [gameLogs]);

  if (!state.players || !me) {
    return <div style={S.app}><div style={S.content}><div style={S.card}>Loading game...</div></div></div>;
  }

  // Config phase
  if (state.phase === "config") {
    return (
      <div style={S.app}>
        <div style={S.overlay} />
        <div style={S.content}>
          <h1 style={{ ...S.title, textAlign: "center", fontSize: 28, marginBottom: 16 }}>TAMSK</h1>
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
          <h1 style={{ ...S.title, textAlign: "center", fontSize: 28, marginBottom: 8 }}>Game Over</h1>
          <div style={{ textAlign: "center", color: "#999", marginBottom: 16, fontSize: 16 }}>
            {winnerPlayer ? `${winnerPlayer.name} wins!` : "It's a draw!"}
          </div>

          <div style={{ display: "flex", gap: 12, marginBottom: 16 }}>
            <PlayerPanel player={me} isCurrent={false} isMe hourglasses={state.hourglasses} level={level} getRemaining={getRemaining} />
            <PlayerPanel player={opp} isCurrent={false} isMe={false} hourglasses={state.hourglasses} level={level} getRemaining={getRemaining} />
          </div>

          <div style={S.card}>
            <HexBoard
              state={state}
              selectedHourglass={null}
              setSelectedHourglass={() => {}}
              submitAction={() => {}}
              myColor={myColor}
              level={level}
              getRemaining={getRemaining}
              showRings={showRings}
              ringWindowSpace={null}
              ringWindowStart={null}
              canPlaceRing={false}
              hoveredHex={null}
              setHoveredHex={null}
            />
            <RingsToggle showRings={showRings} setShowRings={setShowRings} />
          </div>

          <GameLog logs={gameLogs} logRef={logRef} />
        </div>
      </div>
    );
  }

  // Active play
  return (
    <div style={S.app}>
      <div style={S.overlay} />
      <div style={S.content}>
        {/* Header */}
        <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 12 }}>
          <h1 style={{ ...S.title, fontSize: 24 }}>TAMSK</h1>
          <div style={{ display: "flex", gap: 12, alignItems: "center" }}>
            <div style={{
              padding: "4px 12px", borderRadius: 12, fontSize: 12,
              background: `rgba(${level >= 2 ? "255,152,0" : "39,174,96"},0.15)`,
              border: `1px solid ${level >= 2 ? "#ff980044" : "#27ae6044"}`,
              color: level >= 2 ? "#ff9800" : "#27ae60",
            }}>
              Level {level}
            </div>
            <div style={{
              padding: isCurrent ? "6px 16px" : "4px 12px",
              borderRadius: 12, fontSize: isCurrent ? 14 : 12, fontWeight: isCurrent ? 700 : 400,
              background: isCurrent ? "rgba(39,174,96,0.25)" : "rgba(255,255,255,0.05)",
              border: `2px solid ${isCurrent ? "#27ae60" : "#30363d"}`,
              color: isCurrent ? "#27ae60" : "#888",
              animation: isCurrent ? "pulse-turn 2s ease-in-out infinite" : undefined,
            }}>
              {subPhase === "bonus_ring" && isCurrent
                ? "Place bonus ring"
                : subPhase === "bonus_ring" && !isCurrent
                ? `${opp.name} placing bonus ring`
                : subPhase === "ring_window"
                ? "Ring placement window"
                : isCurrent ? "Your turn — move an hourglass" : `${opp.name}'s turn`}
            </div>
          </div>
        </div>

        {/* Player panels */}
        <div style={{ display: "flex", gap: 12, marginBottom: 12 }}>
          <PlayerPanel player={me} isCurrent={state.current_player === myIdx} isMe hourglasses={state.hourglasses} level={level} getRemaining={getRemaining} />
          <PlayerPanel player={opp} isCurrent={state.current_player === oppIdx} isMe={false} hourglasses={state.hourglasses} level={level} getRemaining={getRemaining} />
        </div>

        {/* Pressure timer display — always visible in Level 3 */}
        {level === 3 && pressureTimer && (() => {
          const isActive = pressureTimer.active;
          const atZero = pressureRemaining <= 0;
          const borderColor = isActive ? "#e74c3c" : "#30363d";
          const bgColor = isActive ? "rgba(231,76,60,0.1)" : "rgba(0,0,0,0.2)";
          const textColor = isActive ? "#e74c3c" : "#888";
          const label = isActive ? "PRESSURE ACTIVE" : "PRESSURE TIMER";
          return (
            <div style={{
              ...S.card, textAlign: "center", padding: 12,
              border: `2px solid ${borderColor}`, background: bgColor,
              display: "flex", justifyContent: "center", alignItems: "center", gap: 12,
              animation: canPressure ? "pulse-pressure 1s ease-in-out infinite" : undefined,
            }}>
              <span style={{ fontSize: 14, color: textColor, fontWeight: 700 }}>
                {label}: {formatTime(pressureRemaining)}
              </span>
              {atZero && !isActive && !isCurrent && (
                <span style={{ fontSize: 11, color: "#4caf50", fontWeight: 700 }}>READY</span>
              )}
              {canPressure && (
                <button style={{
                  ...S.btn, ...S.btnP, padding: "6px 16px", fontSize: 13,
                  animation: "pulse-pressure 1s ease-in-out infinite",
                }}
                  onClick={() => submitAction({ kind: "activate_pressure" })}>
                  Flip Timer
                </button>
              )}
            </div>
          );
        })()}

        {/* Auto-pass notification */}
        {autoPassNotice && (
          <div style={{
            ...S.card, textAlign: "center", padding: 12,
            border: "1px solid #ff9800", background: "rgba(255,152,0,0.1)",
          }}>
            <span style={{ fontSize: 14, color: "#ff9800", fontWeight: 700 }}>
              {autoPassNotice}
            </span>
          </div>
        )}

        {/* Board */}
        <div style={S.card}>
          <HexBoard
            state={state}
            selectedHourglass={selectedHourglass}
            setSelectedHourglass={setSelectedHourglass}
            submitAction={submitAction}
            myColor={myColor}
            level={level}
            getRemaining={getRemaining}
            bonusRingSpaces={bonusRingSpaces}
            showRings={showRings}
            ringWindowSpace={ringWindowSpace}
            ringWindowStart={ringWindowStart}
            canPlaceRing={canPlaceRing}
            hoveredHex={hoveredHex}
            setHoveredHex={setHoveredHex}
          />
          <RingsToggle showRings={showRings} setShowRings={setShowRings} />
        </div>

        {/* Action hints */}
        {subPhase === "move" && isCurrent && (
          <div style={{ ...S.card, textAlign: "center", padding: 12 }}>
            <div style={{ fontSize: 13, color: "#888" }}>
              {selectedHourglass
                ? "Click a highlighted space to move there, or click another hourglass"
                : "Click one of your hourglasses to select it"}
            </div>
          </div>
        )}

        {/* Bonus ring popup overlay */}
        {showBonusPopup && (
          <div style={{
            position: "fixed", inset: 0, zIndex: 100,
            background: "rgba(0,0,0,0.6)",
            display: "flex", alignItems: "center", justifyContent: "center",
            pointerEvents: "auto",
          }}>
            <div style={{
              ...S.card, maxWidth: 360, textAlign: "center",
              border: "2px solid #ff9800", background: "rgba(22,27,34,0.98)",
            }}>
              <div style={{ fontSize: 20, color: "#ff9800", fontWeight: 700, marginBottom: 8, fontFamily: font }}>
                Bonus Ring
              </div>
              <div style={{ fontSize: 14, color: "#e8d5a3", marginBottom: 16 }}>
                Pressure timer penalty — click any hex on the board to place your bonus ring.
              </div>
              <button
                style={{ ...S.btn, fontSize: 11, opacity: 0.5, padding: "4px 12px" }}
                onClick={() => submitAction({ kind: "skip_bonus_ring" })}
              >
                Decline
              </button>
            </div>
          </div>
        )}

        {/* Error */}
        {game.error && (
          <div style={{ color: "#e74c3c", padding: 12, background: "rgba(231,76,60,0.1)", borderRadius: 6, marginBottom: 12 }}>
            {game.error}
          </div>
        )}

        {/* Game log */}
        <GameLog logs={gameLogs} logRef={logRef} />
      </div>

      {/* Turn banner overlay */}
      {showTurnBanner && (
        <div style={{
          position: "fixed", inset: 0, zIndex: 200,
          display: "flex", alignItems: "center", justifyContent: "center",
          pointerEvents: "none",
          animation: "fade-out 2s ease-out forwards",
        }}>
          <div style={{
            fontSize: 48, fontWeight: 900, fontFamily: font,
            color: "#c9a84c", textShadow: "0 0 40px rgba(201,168,76,0.6), 0 4px 20px rgba(0,0,0,0.8)",
            letterSpacing: 6,
          }}>
            YOUR TURN
          </div>
        </div>
      )}

      {/* CSS animations */}
      <style>{`
        @keyframes fade-out {
          0% { opacity: 1; }
          60% { opacity: 1; }
          100% { opacity: 0; }
        }
        @keyframes pulse-turn {
          0%, 100% { box-shadow: 0 0 0 0 rgba(39,174,96,0.4); }
          50% { box-shadow: 0 0 12px 4px rgba(39,174,96,0.3); }
        }
        @keyframes pulse-pressure {
          0%, 100% { box-shadow: 0 0 0 0 rgba(201,168,76,0.4); }
          50% { box-shadow: 0 0 12px 4px rgba(201,168,76,0.3); }
        }
        @keyframes ring-fade {
          0% { opacity: 0.8; stroke-width: 4; }
          100% { opacity: 0; stroke-width: 1; }
        }
      `}</style>
    </div>
  );
}

// ─── RINGS TOGGLE ─────────────────────────────────────────────────

function RingsToggle({ showRings, setShowRings }) {
  return (
    <div style={{ display: "flex", justifyContent: "flex-end", marginTop: 8 }}>
      <button
        style={{
          ...S.btn, padding: "3px 10px", fontSize: 11,
          opacity: showRings ? 1 : 0.6,
          background: showRings ? "rgba(201,168,76,0.15)" : "transparent",
          border: `1px solid ${showRings ? "#c9a84c44" : "#30363d"}`,
        }}
        onClick={() => setShowRings(v => !v)}
      >
        {showRings ? "Hide Rings" : "Show Rings"}
      </button>
    </div>
  );
}

// ─── GAME LOG ──────────────────────────────────────────────────────

function GameLog({ logs, logRef }) {
  return (
    <div style={S.card}>
      <div style={{ ...S.cardTitle, fontSize: 14 }}>Game Log</div>
      <div
        ref={logRef}
        style={{
          maxHeight: 160, overflowY: "auto", fontSize: 12, color: "#999",
          lineHeight: 1.6, padding: "0 4px",
        }}
      >
        {logs.length === 0 && <div>No actions yet.</div>}
        {logs.map((msg, i) => (
          <div key={i}>{msg}</div>
        ))}
      </div>
    </div>
  );
}

// ─── APP ───────────────────────────────────────────────────────────

class ErrorBoundary extends React.Component {
  constructor(props) { super(props); this.state = { error: null }; }
  static getDerivedStateFromError(error) { return { error }; }
  render() {
    if (this.state.error) {
      return (
        <div style={{ ...S.app, padding: 40 }}>
          <div style={S.card}>
            <div style={{ color: "#e74c3c", fontSize: 18, marginBottom: 12 }}>GameBoard crashed</div>
            <pre style={{ color: "#ff6b6b", fontSize: 12, whiteSpace: "pre-wrap", wordBreak: "break-all" }}>
              {this.state.error.message}{"\n"}{this.state.error.stack}
            </pre>
            <div style={{ color: "#999", fontSize: 12, marginTop: 12 }}>
              State: <pre style={{ fontSize: 10, maxHeight: 300, overflow: "auto", color: "#888" }}>
                {JSON.stringify(this.props.debugState, null, 2)}
              </pre>
            </div>
          </div>
        </div>
      );
    }
    return this.props.children;
  }
}

export default function App() {
  const game = useGameConnection();

  if (!game.gameStarted) return <Lobby game={game} />;
  if (!game.gameState) return <div style={S.app}><div style={S.content}><div style={S.card}>Waiting for game state...</div></div></div>;
  return (
    <ErrorBoundary debugState={game.gameState}>
      <GameBoard game={game} />
    </ErrorBoundary>
  );
}
