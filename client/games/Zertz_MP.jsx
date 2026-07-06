import React, { useState, useEffect, useRef, useCallback, useMemo } from "react";

// ─── CONFIGURATION ─────────────────────────────────────────────────
import { WS_URL } from "../ws.js";

// ─── CONSTANTS ─────────────────────────────────────────────────────

const AXIAL_DIRS = [[1,0],[-1,0],[0,1],[0,-1],[1,-1],[-1,1]];
const BOARD_RADIUS = 3;

const MARBLE_STYLES = {
  white: { fill: "#f0ead8", stroke: "#bbb", gradient: ["#fff", "#d8d0c0"] },
  gray:  { fill: "#8a8a8a", stroke: "#666", gradient: ["#aaa", "#666"] },
  black: { fill: "#2c3e50", stroke: "#1a252f", gradient: ["#4a5a6a", "#1a252f"] },
};

const HEX_SIZE = 32;
const SQRT3 = Math.sqrt(3);

// ─── HEX MATH ──────────────────────────────────────────────────────

function hexKey(q, r) { return `${q},${r}`; }
function parseHex(key) { const [q, r] = key.split(",").map(Number); return [q, r]; }

function isValid(q, r) { return Math.max(Math.abs(q), Math.abs(r), Math.abs(q + r)) <= BOARD_RADIUS; }

function hexToPixel(q, r) {
  return {
    x: HEX_SIZE * 1.5 * q,
    y: HEX_SIZE * (SQRT3 / 2 * q + SQRT3 * r),
  };
}

function allInitialPositions() {
  const positions = [];
  for (let q = -BOARD_RADIUS; q <= BOARD_RADIUS; q++) {
    for (let r = -BOARD_RADIUS; r <= BOARD_RADIUS; r++) {
      if (isValid(q, r)) positions.push([q, r]);
    }
  }
  return positions;
}

const ALL_INITIAL_POSITIONS = allInitialPositions();

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
      setConnected(true); setError(null);
      if (tokenRef.current) ws.send(JSON.stringify({ type: "reconnect", token: tokenRef.current }));
      onOpen?.();
    };
    ws.onmessage = (evt) => {
      const msg = JSON.parse(evt.data);
      switch (msg.type) {
        case "created": case "joined":
          setRoomCode(msg.room_code); setPlayerId(msg.player_id);
          setToken(msg.token); tokenRef.current = msg.token; sessionStorage.setItem("game_token", msg.token);
          ws.send(JSON.stringify({ type: "auth", token: msg.token })); break;
        case "authenticated": setRoomCode(msg.room_code); setPlayerId(msg.player_id); setIsHost(msg.is_host); setGameStarted(msg.game_started); break;
        case "lobby_update": setLobby(msg.players); if (msg.game_started !== undefined) setGameStarted(msg.game_started); break;
        case "game_started": setGameStarted(true); break;
        case "game_state": setGameState(msg.state); setPhaseInfo(msg.phase_info); setYourTurn(msg.your_turn); setWaitingFor(msg.waiting_for || []); break;
        case "game_log": setGameLogs(prev => [...prev, ...msg.messages]); break;
        case "game_over": setGameOver(true); break;
        case "action_error": setError(msg.message); break;
        case "error": setError(msg.message); break;
      }
    };
    ws.onclose = () => { setConnected(false); setTimeout(() => { if (tokenRef.current) connect(); }, 2000); };
  }, []);


  const createRoom = (name) => { connect(() => send({ type: "create", game: "zertz", name })); };
  const joinRoom = (code, name) => { connect(() => send({ type: "join", room_code: code.toUpperCase(), name })); };

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

// ─── RING CELL COMPONENT ──────────────────────────────────────────

function RingCell({ q, r, marble, isTarget, isFreeRing, isCaptureSrc, onClick }) {
  const { x, y } = hexToPixel(q, r);
  const ringR = HEX_SIZE * 0.85;
  const marbleR = HEX_SIZE * 0.6;

  let ringStroke = "#444";
  let ringFill = "rgba(40,40,40,0.6)";
  if (isFreeRing) { ringStroke = "#e74c3c"; ringFill = "rgba(231,76,60,0.1)"; }
  if (isTarget) { ringStroke = "#4caf50"; ringFill = "rgba(76,175,80,0.15)"; }
  if (isCaptureSrc) { ringStroke = "#ffeb3b"; }

  const ms = marble ? MARBLE_STYLES[marble] : null;

  return (
    <g onClick={onClick} style={{ cursor: onClick ? "pointer" : "default" }}>
      {/* Ring */}
      <circle cx={x} cy={y} r={ringR} fill={ringFill} stroke={ringStroke} strokeWidth={2} />
      {/* Marble */}
      {ms && (
        <circle cx={x} cy={y} r={marbleR} fill={ms.fill} stroke={ms.stroke} strokeWidth={1.5} />
      )}
      {/* Target indicator */}
      {isTarget && !marble && (
        <circle cx={x} cy={y} r={8} fill="rgba(76,175,80,0.5)" />
      )}
    </g>
  );
}

// ─── HEX BOARD COMPONENT ──────────────────────────────────────────

function HexBoard({ state, submitAction, selectedColor, setSelectedColor }) {
  const board = state.board;
  const validActions = state.valid_actions || [];
  const subPhase = state.sub_phase;
  const isMyTurn = state.current_player === getMyPlayerIdx(state);

  const [selectedFrom, setSelectedFrom] = useState(null);

  useEffect(() => { setSelectedFrom(null); }, [subPhase, state.current_player]);

  // Valid targets
  const captureTargets = useMemo(() => {
    if (subPhase !== "place_or_capture" && subPhase !== "capture_sequence") return {};
    const targets = {};
    validActions.filter(a => a.kind === "capture").forEach(a => {
      if (!targets[a.from]) targets[a.from] = [];
      targets[a.from].push(a.to);
    });
    return targets;
  }, [subPhase, validActions]);

  const captureSources = useMemo(() => new Set(Object.keys(captureTargets)), [captureTargets]);

  const placeTargets = useMemo(() => {
    if (subPhase !== "place_or_capture" || !selectedColor) return new Set();
    return new Set(
      validActions
        .filter(a => a.kind === "place_marble" && a.color === selectedColor)
        .map(a => a.position)
    );
  }, [subPhase, validActions, selectedColor]);

  const freeRings = useMemo(() => {
    if (subPhase !== "remove_ring") return new Set();
    return new Set(validActions.filter(a => a.kind === "remove_ring").map(a => a.ring));
  }, [subPhase, validActions]);

  const selectedDests = useMemo(() => {
    if (!selectedFrom || !captureTargets[selectedFrom]) return new Set();
    return new Set(captureTargets[selectedFrom]);
  }, [selectedFrom, captureTargets]);

  const handleClick = useCallback((key) => {
    if (!isMyTurn) return;

    if (subPhase === "remove_ring" && freeRings.has(key)) {
      submitAction({ kind: "remove_ring", ring: key });
      return;
    }

    if ((subPhase === "place_or_capture" || subPhase === "capture_sequence") && captureSources.size > 0) {
      // Capture mode
      if (selectedFrom && selectedDests.has(key)) {
        submitAction({ kind: "capture", from: selectedFrom, to: key });
        setSelectedFrom(null);
        return;
      }
      if (captureSources.has(key)) {
        setSelectedFrom(key === selectedFrom ? null : key);
        return;
      }
      setSelectedFrom(null);
      return;
    }

    if (subPhase === "place_or_capture" && selectedColor && placeTargets.has(key)) {
      submitAction({ kind: "place_marble", color: selectedColor, position: key });
      setSelectedColor(null);
      return;
    }
  }, [isMyTurn, subPhase, freeRings, captureSources, selectedFrom, selectedDests, selectedColor, placeTargets, submitAction, setSelectedColor]);

  // SVG viewBox
  const allPixels = ALL_INITIAL_POSITIONS.map(([q, r]) => hexToPixel(q, r));
  const pad = HEX_SIZE + 15;
  const minX = Math.min(...allPixels.map(p => p.x)) - pad;
  const maxX = Math.max(...allPixels.map(p => p.x)) + pad;
  const minY = Math.min(...allPixels.map(p => p.y)) - pad;
  const maxY = Math.max(...allPixels.map(p => p.y)) + pad;
  const viewBox = `${minX} ${minY} ${maxX - minX} ${maxY - minY}`;

  // Show ghost positions for removed rings
  const boardKeys = new Set(Object.keys(board));

  return (
    <svg viewBox={viewBox} style={{ width: "100%", maxHeight: 420, display: "block" }}>
      {/* Ghost positions (removed rings) */}
      {ALL_INITIAL_POSITIONS.map(([q, r]) => {
        const key = hexKey(q, r);
        if (boardKeys.has(key)) return null;
        const { x, y } = hexToPixel(q, r);
        return <circle key={`ghost-${key}`} cx={x} cy={y} r={HEX_SIZE * 0.3} fill="#1a1a1a" opacity={0.3} />;
      })}
      {/* Active rings */}
      {ALL_INITIAL_POSITIONS.map(([q, r]) => {
        const key = hexKey(q, r);
        if (!boardKeys.has(key)) return null;
        const marble = board[key];
        const isTarget = placeTargets.has(key) || selectedDests.has(key);
        const isFree = freeRings.has(key);
        const isCapSrc = captureSources.has(key);
        const clickable = isMyTurn && (isTarget || isFree || isCapSrc);

        return (
          <RingCell
            key={key} q={q} r={r}
            marble={marble}
            isTarget={isTarget}
            isFreeRing={isFree}
            isCaptureSrc={key === selectedFrom}
            onClick={clickable ? () => handleClick(key) : null}
          />
        );
      })}
    </svg>
  );
}

// ─── MARBLE POOL COMPONENT ─────────────────────────────────────────

function MarblePool({ pool, selectedColor, setSelectedColor, canPlace }) {
  return (
    <div style={{ display: "flex", gap: 16, justifyContent: "center", alignItems: "center" }}>
      {["white", "gray", "black"].map(color => {
        const count = pool[color] || 0;
        const ms = MARBLE_STYLES[color];
        const selected = selectedColor === color;
        const clickable = canPlace && count > 0;

        return (
          <div key={color}
            onClick={clickable ? () => setSelectedColor(selected ? null : color) : undefined}
            style={{
              display: "flex", flexDirection: "column", alignItems: "center", gap: 4,
              cursor: clickable ? "pointer" : "default",
              opacity: count > 0 ? 1 : 0.3,
              padding: "6px 12px", borderRadius: 8,
              border: `2px solid ${selected ? "#c9a84c" : "transparent"}`,
              background: selected ? "rgba(201,168,76,0.1)" : "transparent",
            }}
          >
            <svg width={32} height={32}>
              <circle cx={16} cy={16} r={12} fill={ms.fill} stroke={ms.stroke} strokeWidth={1.5} />
            </svg>
            <span style={{ fontSize: 12, color: "#888" }}>{count}</span>
          </div>
        );
      })}
    </div>
  );
}

// ─── PLAYER PANEL COMPONENT ────────────────────────────────────────

function PlayerPanel({ player, isCurrent, isMe, winConditions }) {
  const cap = player.captured;
  return (
    <div style={{
      ...S.card, flex: 1, padding: 12,
      border: `2px solid ${isCurrent ? "#c9a84c" : "#30363d"}`,
      boxShadow: isCurrent ? "0 0 12px rgba(201,168,76,0.3)" : S.card.boxShadow,
    }}>
      <div style={{ display: "flex", alignItems: "center", gap: 8, marginBottom: 6 }}>
        <span style={{ fontWeight: 700, color: isMe ? "#c9a84c" : "#e8d5a3", fontSize: 14 }}>
          {player.name} {isMe ? "(You)" : ""}
        </span>
      </div>
      <div style={{ display: "flex", gap: 12, fontSize: 12 }}>
        {["white", "gray", "black"].map(color => {
          const ms = MARBLE_STYLES[color];
          const count = cap[color];
          const target = winConditions[color];
          return (
            <div key={color} style={{ display: "flex", alignItems: "center", gap: 4 }}>
              <svg width={14} height={14}>
                <circle cx={7} cy={7} r={5} fill={ms.fill} stroke={ms.stroke} strokeWidth={1} />
              </svg>
              <span style={{ color: count >= target ? "#27ae60" : "#888" }}>
                {count}/{target}
              </span>
            </div>
          );
        })}
        <span style={{ color: "#555" }}>|</span>
        <span style={{
          color: (cap.white >= winConditions.each && cap.gray >= winConditions.each && cap.black >= winConditions.each) ? "#27ae60" : "#888"
        }}>
          Each: {Math.min(cap.white, cap.gray, cap.black)}/{winConditions.each}
        </span>
      </div>
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
      <div style={S.app}><div style={S.overlay} /><div style={S.content}>
        <div style={{ textAlign: "center", paddingTop: 60 }}>
          <h1 style={{ ...S.title, fontSize: 48, marginBottom: 8 }}>ZERTZ</h1>
          <p style={{ color: "#888", fontSize: 14, marginBottom: 32 }}>A game of the GIPF project</p>
          <div style={{ display: "flex", gap: 12, justifyContent: "center" }}>
            <button style={bs(true)} onClick={() => setMode("create")}>Create Room</button>
            <button style={bs(false)} onClick={() => setMode("join")}>Join Room</button>
          </div>
        </div>
      </div></div>
    );
  }

  if (!game.roomCode) {
    return (
      <div style={S.app}><div style={S.overlay} /><div style={S.content}>
        <div style={{ textAlign: "center", paddingTop: 60 }}>
          <h1 style={{ ...S.title, marginBottom: 24 }}>ZERTZ</h1>
          <div style={{ ...S.card, maxWidth: 360, margin: "0 auto" }}>
            <div style={S.cardTitle}>{mode === "create" ? "Create Room" : "Join Room"}</div>
            <div style={{ display: "flex", flexDirection: "column", gap: 12 }}>
              <input style={S.input} placeholder="Your name" value={name}
                onChange={e => setName(e.target.value)}
                onKeyDown={e => { if (e.key === "Enter" && name.trim()) { if (mode === "create") game.createRoom(name.trim()); else if (joinCode.trim()) game.joinRoom(joinCode.trim(), name.trim()); }}}
              />
              {mode === "join" && <input style={S.input} placeholder="Room code" value={joinCode}
                onChange={e => setJoinCode(e.target.value.toUpperCase())}
                onKeyDown={e => { if (e.key === "Enter" && name.trim() && joinCode.trim()) game.joinRoom(joinCode.trim(), name.trim()); }}
              />}
              <div style={{ display: "flex", gap: 8 }}>
                <button style={bs(false)} onClick={() => setMode(null)}>Back</button>
                <button style={bs(true, !name.trim())} disabled={!name.trim()}
                  onClick={() => { if (mode === "create") game.createRoom(name.trim()); else if (joinCode.trim()) game.joinRoom(joinCode.trim(), name.trim()); }}>
                  {mode === "create" ? "Create" : "Join"}
                </button>
              </div>
            </div>
          </div>
          {game.error && <div style={{ color: "#e74c3c", marginTop: 12, fontSize: 13 }}>{game.error}</div>}
        </div>
      </div></div>
    );
  }

  return (
    <div style={S.app}><div style={S.overlay} /><div style={S.content}>
      <div style={{ textAlign: "center", paddingTop: 60 }}>
        <h1 style={{ ...S.title, marginBottom: 24 }}>ZERTZ</h1>
        <div style={{ ...S.card, maxWidth: 400, margin: "0 auto" }}>
          <div style={S.cardTitle}>Room: {game.roomCode}</div>
          <div style={{ marginBottom: 16 }}>
            {game.lobby.map((p, i) => (
              <div key={i} style={{ padding: "6px 12px", borderRadius: 6, marginBottom: 4,
                background: "rgba(255,255,255,0.05)", fontSize: 14, display: "flex", justifyContent: "space-between" }}>
                <span>{p.name}</span>
                <span style={{ color: "#888", fontSize: 12 }}>{p.is_host ? "Host" : "Player"}</span>
              </div>
            ))}
          </div>
          {game.isHost && game.lobby.length >= 2 && <button style={bs(true)} onClick={() => game.startGame()}>Start Game</button>}
          {game.isHost && game.lobby.length < 2 && <div style={{ color: "#888", fontSize: 13 }}>Waiting for another player...</div>}
          {!game.isHost && <div style={{ color: "#888", fontSize: 13 }}>Waiting for host to start...</div>}
        </div>
        {game.error && <div style={{ color: "#e74c3c", marginTop: 12, fontSize: 13 }}>{game.error}</div>}
      </div>
    </div></div>
  );
}

// ─── GAME BOARD COMPONENT ─────────────────────────────────────────

function GameBoard({ game }) {
  const { gameState: state, phaseInfo, gameLogs, submitAction } = game;
  const [selectedColor, setSelectedColor] = useState(null);
  const logRef = useRef(null);

  // All hooks before null guard
  useEffect(() => {
    if (logRef.current) logRef.current.scrollTop = logRef.current.scrollHeight;
  }, [gameLogs]);
  useEffect(() => { setSelectedColor(null); }, [state?.current_player, state?.sub_phase]);

  if (!state || !state.players) {
    return <div style={S.app}><div style={S.content}><div style={S.card}>Loading game...</div></div></div>;
  }

  const myIdx = getMyPlayerIdx(state);
  const oppIdx = 1 - myIdx;
  const me = state.players[myIdx];
  const opp = state.players[oppIdx];
  const phase = state.phase;
  const subPhase = state.sub_phase;
  const isMyTurn = state.current_player === myIdx;
  const mustCapture = state.must_capture;
  const winConditions = state.win_conditions || { each: 3, white: 4, gray: 5, black: 6 };

  // Config phase
  if (phase === "config") {
    return (
      <div style={S.app}><div style={S.overlay} /><div style={S.content}>
        <h1 style={{ ...S.title, textAlign: "center", fontSize: 28, marginBottom: 16 }}>ZERTZ</h1>
        <div style={{ ...S.card, textAlign: "center" }}>
          <div style={S.cardTitle}>Select Game Mode</div>
          {myIdx === 0 ? (
            <div style={{ display: "flex", gap: 12, justifyContent: "center" }}>
              <button style={bs(true)} onClick={() => submitAction({ kind: "set_mode", mode: "normal" })}>Normal</button>
              <button style={bs(false)} onClick={() => submitAction({ kind: "set_mode", mode: "blitz" })}>Blitz</button>
            </div>
          ) : <div style={{ color: "#888", fontSize: 14 }}>Waiting for host...</div>}
        </div>
      </div></div>
    );
  }

  // Game over
  if (state.game_over) {
    const winnerPlayer = state.players.find(p => p.player_id === state.winner);
    return (
      <div style={S.app}><div style={S.overlay} /><div style={S.content}>
        <h1 style={{ ...S.title, textAlign: "center", fontSize: 28, marginBottom: 16 }}>ZERTZ — Game Over</h1>
        <div style={{ ...S.card, textAlign: "center" }}>
          <div style={{ fontSize: 22, fontWeight: 700, marginBottom: 12, color: "#c9a84c" }}>
            {winnerPlayer ? `${winnerPlayer.name} wins!` : "It's a draw!"}
          </div>
          <div style={{ display: "flex", gap: 12, justifyContent: "center", marginBottom: 16 }}>
            <PlayerPanel player={me} isCurrent={false} isMe winConditions={winConditions} />
            <PlayerPanel player={opp} isCurrent={false} isMe={false} winConditions={winConditions} />
          </div>
        </div>
        <div style={S.card}>
          <HexBoard state={state} submitAction={() => {}} selectedColor={null} setSelectedColor={() => {}} />
        </div>
        <div style={S.card}>
          <div style={S.cardTitle}>Game Log</div>
          <div style={{ maxHeight: 200, overflowY: "auto", fontSize: 12, color: "#888" }}>
            {gameLogs.map((msg, i) => <div key={i} style={{ padding: "2px 0" }}>{msg}</div>)}
          </div>
        </div>
      </div></div>
    );
  }

  // Active game
  const canPlace = isMyTurn && subPhase === "place_or_capture" && !mustCapture;

  return (
    <div style={S.app}>
      <div style={S.overlay} />
      <div style={S.content}>
        {/* Header */}
        <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 12 }}>
          <h1 style={{ ...S.title, fontSize: 24, margin: 0 }}>ZERTZ</h1>
          <div style={{ display: "flex", gap: 8, alignItems: "center" }}>
            <div style={{
              padding: "4px 12px", borderRadius: 12, fontSize: 12,
              background: "rgba(255,255,255,0.05)", border: "1px solid #30363d", color: "#888",
            }}>
              Rings: {Object.keys(state.board).length} &middot; {state.mode === "blitz" ? "Blitz" : "Normal"}
            </div>
            <div style={{
              padding: "4px 12px", borderRadius: 12, fontSize: 12,
              background: isMyTurn ? "rgba(39,174,96,0.2)" : "rgba(255,255,255,0.05)",
              border: `1px solid ${isMyTurn ? "#27ae60" : "#30363d"}`,
              color: isMyTurn ? "#27ae60" : "#888",
            }}>
              {phaseInfo?.description || (isMyTurn ? "Your turn" : `${opp.name}'s turn`)}
            </div>
          </div>
        </div>

        {/* Player panels */}
        <div style={{ display: "flex", gap: 12, marginBottom: 12 }}>
          <PlayerPanel player={me} isCurrent={state.current_player === myIdx} isMe winConditions={winConditions} />
          <PlayerPanel player={opp} isCurrent={state.current_player === oppIdx} isMe={false} winConditions={winConditions} />
        </div>

        {/* Marble pool */}
        {subPhase === "place_or_capture" && !mustCapture && (
          <div style={{ ...S.card, padding: 12 }}>
            <div style={{ textAlign: "center", fontSize: 12, color: "#888", marginBottom: 8 }}>
              {isMyTurn ? "Select a marble to place:" : "Marble pool:"}
            </div>
            <MarblePool pool={state.pool} selectedColor={selectedColor}
              setSelectedColor={setSelectedColor} canPlace={canPlace} />
          </div>
        )}

        {/* Must capture warning */}
        {mustCapture && isMyTurn && subPhase === "place_or_capture" && (
          <div style={{
            ...S.card, textAlign: "center", padding: 12,
            border: "1px solid #e67e22", background: "rgba(230,126,34,0.1)",
          }}>
            <span style={{ fontSize: 13, color: "#e67e22", fontWeight: 700 }}>
              Capture is mandatory! Click a marble to select, then click destination.
            </span>
          </div>
        )}

        {/* Board */}
        <div style={S.card}>
          <HexBoard state={state} submitAction={submitAction}
            selectedColor={selectedColor} setSelectedColor={setSelectedColor} />
        </div>

        {/* Action hints */}
        <div style={{ ...S.card, display: "flex", gap: 12, justifyContent: "center", flexWrap: "wrap" }}>
          {isMyTurn && subPhase === "place_or_capture" && !mustCapture && !selectedColor && (
            <div style={{ fontSize: 13, color: "#888" }}>Select a marble color from the pool above</div>
          )}
          {isMyTurn && subPhase === "place_or_capture" && !mustCapture && selectedColor && (
            <div style={{ fontSize: 13, color: "#888" }}>Click an empty ring to place your {selectedColor} marble</div>
          )}
          {isMyTurn && subPhase === "remove_ring" && (
            <div style={{ fontSize: 13, color: "#e74c3c" }}>Click a red-highlighted ring to remove it</div>
          )}
          {isMyTurn && subPhase === "capture_sequence" && (
            <div style={{ fontSize: 13, color: "#e67e22" }}>Continue jumping! Click the next destination.</div>
          )}
          {!isMyTurn && (
            <div style={{ fontSize: 13, color: "#888" }}>Waiting for {opp.name}...</div>
          )}
        </div>

        {/* Error */}
        {game.error && (
          <div style={{ ...S.card, textAlign: "center", padding: 12, border: "1px solid #e74c3c", background: "rgba(231,76,60,0.1)" }}>
            <span style={{ fontSize: 13, color: "#e74c3c" }}>{game.error}</span>
          </div>
        )}

        {/* Game log */}
        <div style={S.card}>
          <div style={S.cardTitle}>Game Log</div>
          <div ref={logRef} style={{ maxHeight: 150, overflowY: "auto", fontSize: 12, color: "#888" }}>
            {gameLogs.length === 0
              ? <div style={{ fontStyle: "italic" }}>Game started...</div>
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
