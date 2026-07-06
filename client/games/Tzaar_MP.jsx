import React, { useState, useEffect, useRef, useCallback, useMemo } from "react";

// ─── CONFIGURATION ─────────────────────────────────────────────────
import { WS_URL } from "../ws.js";

// ─── CONSTANTS ─────────────────────────────────────────────────────

const AXIAL_DIRS = [[1,0],[-1,0],[0,1],[0,-1],[1,-1],[-1,1]];
const BOARD_MAX_RADIUS = 4;

const PIECE_STYLES = {
  white: { fill: "#e8e0d0", stroke: "#999", text: "#333" },
  black: { fill: "#2c3e50", stroke: "#1a252f", text: "#e8d5a3" },
};

const TYPE_LABELS = { tzaar: "Z", tzarra: "A", tott: "T" };
const TYPE_COLORS = { tzaar: "#c0392b", tzarra: "#2980b9", tott: "#27ae60" };

const HEX_SIZE = 28;
const SQRT3 = Math.sqrt(3);

// ─── HEX MATH ──────────────────────────────────────────────────────

function hexKey(q, r) { return `${q},${r}`; }
function parseHex(key) { const [q, r] = key.split(",").map(Number); return [q, r]; }
function hexDist(q, r) { return Math.max(Math.abs(q), Math.abs(r), Math.abs(q + r)); }
function isValid(q, r) { const d = hexDist(q, r); return d >= 1 && d <= BOARD_MAX_RADIUS; }

function hexToPixel(q, r) {
  return { x: HEX_SIZE * 1.5 * q, y: HEX_SIZE * (SQRT3 / 2 * q + SQRT3 * r) };
}

function allPositions() {
  const p = [];
  for (let q = -BOARD_MAX_RADIUS; q <= BOARD_MAX_RADIUS; q++)
    for (let r = -BOARD_MAX_RADIUS; r <= BOARD_MAX_RADIUS; r++)
      if (isValid(q, r)) p.push([q, r]);
  return p;
}
const ALL_POSITIONS = allPositions();

// Grid lines
function gridLines() {
  const lines = [];
  const s = new Set(ALL_POSITIONS.map(([q,r]) => hexKey(q,r)));
  const dirs = [[1,0],[0,1],[1,-1]];
  for (const [q, r] of ALL_POSITIONS) {
    for (const [dq, dr] of dirs) {
      const nk = hexKey(q+dq, r+dr);
      if (s.has(nk)) {
        const f = hexToPixel(q, r), t = hexToPixel(q+dq, r+dr);
        lines.push({ x1: f.x, y1: f.y, x2: t.x, y2: t.y });
      }
    }
  }
  return lines;
}
const GRID_LINES = gridLines();

// ─── THEME & STYLES ────────────────────────────────────────────────

const font = `'Cinzel', Georgia, serif`;
const S = {
  app: { fontFamily: font, minHeight: "100vh", background: "linear-gradient(160deg, #0d1117 0%, #161b22 30%, #0d1117 100%)", color: "#e8d5a3", position: "relative", overflow: "hidden" },
  overlay: { position: "fixed", inset: 0, pointerEvents: "none", zIndex: 0, backgroundImage: `repeating-linear-gradient(45deg, transparent, transparent 35px, rgba(201,168,76,0.02) 35px, rgba(201,168,76,0.02) 70px)` },
  content: { position: "relative", zIndex: 1, maxWidth: 1100, margin: "0 auto", padding: "16px 20px" },
  card: { background: "linear-gradient(135deg, rgba(22,27,34,0.95) 0%, rgba(13,17,23,0.98) 100%)", border: "1px solid #30363d", borderRadius: 8, padding: 20, marginBottom: 16, boxShadow: "0 4px 20px rgba(0,0,0,0.5)" },
  cardTitle: { fontFamily: font, fontSize: 18, color: "#c9a84c", marginBottom: 12, borderBottom: "1px solid #30363d", paddingBottom: 8 },
  btn: { fontFamily: font, fontSize: 14, padding: "8px 20px", borderRadius: 6, border: "1px solid #30363d", background: "linear-gradient(135deg, #21262d 0%, #161b22 100%)", color: "#e8d5a3", cursor: "pointer", transition: "all 0.2s", fontWeight: 600 },
  btnP: { background: "linear-gradient(135deg, #c9a84c 0%, #a08030 100%)", color: "#0d1117", border: "1px solid #c9a84c" },
  dis: { opacity: 0.4, cursor: "not-allowed" },
  title: { fontFamily: font, fontSize: 36, fontWeight: 700, color: "#c9a84c", textShadow: "0 2px 8px rgba(0,0,0,0.5)", margin: 0, letterSpacing: 3 },
  input: { flex: 1, fontFamily: font, fontSize: 14, padding: "8px 12px", borderRadius: 6, border: "1px solid #30363d", background: "rgba(0,0,0,0.3)", color: "#e8d5a3", outline: "none" },
};
function bs(primary, disabled) { return { ...S.btn, ...(primary ? S.btnP : {}), ...(disabled ? S.dis : {}) }; }

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

  const send = useCallback((msg) => { if (wsRef.current?.readyState === WebSocket.OPEN) wsRef.current.send(JSON.stringify(msg)); }, []);

  const connect = useCallback((onOpen) => {
    if (wsRef.current?.readyState === WebSocket.OPEN) { onOpen?.(); return; }
    const ws = new WebSocket(WS_URL); wsRef.current = ws;
    ws.onopen = () => { setConnected(true); setError(null); if (tokenRef.current) ws.send(JSON.stringify({ type: "reconnect", token: tokenRef.current })); onOpen?.(); };
    ws.onmessage = (evt) => {
      const msg = JSON.parse(evt.data);
      switch (msg.type) {
        case "created": case "joined": setRoomCode(msg.room_code); setPlayerId(msg.player_id); setToken(msg.token); tokenRef.current = msg.token; sessionStorage.setItem("game_token", msg.token); ws.send(JSON.stringify({ type: "auth", token: msg.token })); break;
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


  const createRoom = (name) => { connect(() => send({ type: "create", game: "tzaar", name })); };
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

  return { connected, roomCode, playerId, token, isHost, lobby, gameStarted, gameState, phaseInfo, yourTurn, waitingFor, gameLogs, gameOver, error, createRoom, joinRoom, startGame, submitAction };
}

// ─── PIECE CELL COMPONENT ──────────────────────────────────────────

function PieceCell({ q, r, piece, isTarget, isSelected, onClick }) {
  const { x, y } = hexToPixel(q, r);
  const pieceR = HEX_SIZE * 0.65;

  if (!piece) {
    return (
      <g onClick={onClick} style={{ cursor: onClick ? "pointer" : "default" }}>
        <circle cx={x} cy={y} r={4} fill={isTarget ? "rgba(76,175,80,0.5)" : "#444"} />
        {isTarget && <circle cx={x} cy={y} r={8} fill="none" stroke="#4caf50" strokeWidth={2} />}
      </g>
    );
  }

  const ps = PIECE_STYLES[piece.color];
  const tc = TYPE_COLORS[piece.type];
  const label = TYPE_LABELS[piece.type];

  return (
    <g onClick={onClick} style={{ cursor: onClick ? "pointer" : "default" }}>
      {/* Target highlight */}
      {isTarget && <circle cx={x} cy={y} r={pieceR + 4} fill="none" stroke="#4caf50" strokeWidth={2.5} />}
      {/* Selection highlight */}
      {isSelected && <circle cx={x} cy={y} r={pieceR + 4} fill="none" stroke="#ffeb3b" strokeWidth={3} />}
      {/* Piece circle */}
      <circle cx={x} cy={y} r={pieceR} fill={ps.fill} stroke={ps.stroke} strokeWidth={2} />
      {/* Type indicator */}
      <text x={x} y={piece.height > 1 ? y - 2 : y + 1} textAnchor="middle" dominantBaseline="middle"
        fill={tc} fontSize={11} fontWeight={700} fontFamily="sans-serif">{label}</text>
      {/* Height number */}
      {piece.height > 1 && (
        <text x={x} y={y + 10} textAnchor="middle" dominantBaseline="middle"
          fill={ps.text} fontSize={9} fontWeight={700} fontFamily="monospace">{piece.height}</text>
      )}
    </g>
  );
}

// ─── HEX BOARD COMPONENT ──────────────────────────────────────────

function HexBoard({ state, submitAction, actionMode }) {
  const board = state.board;
  const validActions = state.valid_actions || [];
  const subPhase = state.sub_phase;
  const isMyTurn = state.current_player === getMyPlayerIdx(state);

  const [selectedFrom, setSelectedFrom] = useState(null);

  useEffect(() => { setSelectedFrom(null); }, [subPhase, state.current_player, actionMode]);

  // Compute sources and targets
  const actionsByFrom = useMemo(() => {
    const byFrom = {};
    const kinds = actionMode === "stack" ? ["stack"] : actionMode === "capture" ? ["capture"] : ["capture", "stack"];
    validActions.filter(a => kinds.includes(a.kind)).forEach(a => {
      if (!byFrom[a.from]) byFrom[a.from] = [];
      byFrom[a.from].push(a);
    });
    return byFrom;
  }, [validActions, actionMode]);

  const sources = useMemo(() => new Set(Object.keys(actionsByFrom)), [actionsByFrom]);

  const targets = useMemo(() => {
    if (!selectedFrom || !actionsByFrom[selectedFrom]) return new Set();
    return new Set(actionsByFrom[selectedFrom].map(a => a.to));
  }, [selectedFrom, actionsByFrom]);

  const handleClick = useCallback((key) => {
    if (!isMyTurn) return;

    if (selectedFrom && targets.has(key)) {
      const action = actionsByFrom[selectedFrom].find(a => a.to === key);
      if (action) submitAction(action);
      setSelectedFrom(null);
      return;
    }

    if (sources.has(key)) {
      setSelectedFrom(key === selectedFrom ? null : key);
      return;
    }

    setSelectedFrom(null);
  }, [isMyTurn, selectedFrom, targets, sources, actionsByFrom, submitAction]);

  const allPixels = ALL_POSITIONS.map(([q, r]) => hexToPixel(q, r));
  const pad = HEX_SIZE + 15;
  const minX = Math.min(...allPixels.map(p => p.x)) - pad;
  const maxX = Math.max(...allPixels.map(p => p.x)) + pad;
  const minY = Math.min(...allPixels.map(p => p.y)) - pad;
  const maxY = Math.max(...allPixels.map(p => p.y)) + pad;
  const viewBox = `${minX} ${minY} ${maxX - minX} ${maxY - minY}`;

  return (
    <svg viewBox={viewBox} style={{ width: "100%", maxHeight: 480, display: "block" }}>
      {/* Center hole indicator */}
      <circle cx={0} cy={0} r={HEX_SIZE * 0.4} fill="#1a1a1a" stroke="#333" strokeWidth={1} strokeDasharray="3 3" />
      {/* Grid lines */}
      {GRID_LINES.map((l, i) => <line key={i} x1={l.x1} y1={l.y1} x2={l.x2} y2={l.y2} stroke="#333" strokeWidth={1} />)}
      {/* Pieces */}
      {ALL_POSITIONS.map(([q, r]) => {
        const key = hexKey(q, r);
        const piece = board[key] || null;
        const isTarget = targets.has(key);
        const isSelected = key === selectedFrom;
        const isSource = sources.has(key);
        const clickable = isMyTurn && (isTarget || isSource);

        return (
          <PieceCell key={key} q={q} r={r} piece={piece}
            isTarget={isTarget} isSelected={isSelected}
            onClick={clickable ? () => handleClick(key) : null}
          />
        );
      })}
    </svg>
  );
}

// ─── PLAYER PANEL COMPONENT ────────────────────────────────────────

function PlayerPanel({ player, isCurrent, isMe, board }) {
  const ps = PIECE_STYLES[player.color];
  const counts = useMemo(() => {
    const c = { tzaar: 0, tzarra: 0, tott: 0 };
    Object.values(board).forEach(p => { if (p && p.color === player.color) c[p.type]++; });
    return c;
  }, [board, player.color]);

  return (
    <div style={{
      ...S.card, flex: 1, padding: 12,
      border: `2px solid ${isCurrent ? "#c9a84c" : "#30363d"}`,
      boxShadow: isCurrent ? "0 0 12px rgba(201,168,76,0.3)" : S.card.boxShadow,
    }}>
      <div style={{ display: "flex", alignItems: "center", gap: 8, marginBottom: 6 }}>
        <div style={{ width: 16, height: 16, borderRadius: "50%", background: ps.fill, border: `2px solid ${ps.stroke}` }} />
        <span style={{ fontWeight: 700, color: isMe ? "#c9a84c" : "#e8d5a3", fontSize: 14 }}>
          {player.name} {isMe ? "(You)" : ""}
        </span>
      </div>
      <div style={{ display: "flex", gap: 12, fontSize: 12 }}>
        {["tzaar", "tzarra", "tott"].map(t => (
          <span key={t} style={{ color: counts[t] > 0 ? TYPE_COLORS[t] : "#555" }}>
            {TYPE_LABELS[t]}: {counts[t]}
          </span>
        ))}
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
          <h1 style={{ ...S.title, fontSize: 48, marginBottom: 8 }}>TZAAR</h1>
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
          <h1 style={{ ...S.title, marginBottom: 24 }}>TZAAR</h1>
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
        <h1 style={{ ...S.title, marginBottom: 24 }}>TZAAR</h1>
        <div style={{ ...S.card, maxWidth: 400, margin: "0 auto" }}>
          <div style={S.cardTitle}>Room: {game.roomCode}</div>
          <div style={{ marginBottom: 16 }}>
            {game.lobby.map((p, i) => (
              <div key={i} style={{ padding: "6px 12px", borderRadius: 6, marginBottom: 4, background: "rgba(255,255,255,0.05)", fontSize: 14, display: "flex", justifyContent: "space-between" }}>
                <span>{p.name}</span><span style={{ color: "#888", fontSize: 12 }}>{p.is_host ? "Host" : "Player"}</span>
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
  const [actionMode, setActionMode] = useState("capture"); // "capture" | "stack"
  const logRef = useRef(null);

  useEffect(() => { if (logRef.current) logRef.current.scrollTop = logRef.current.scrollHeight; }, [gameLogs]);
  useEffect(() => { setActionMode("capture"); }, [state?.current_player, state?.sub_phase]);

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
  const validActions = state.valid_actions || [];
  const canPass = validActions.some(a => a.kind === "pass");

  // Config phase
  if (phase === "config") {
    return (
      <div style={S.app}><div style={S.overlay} /><div style={S.content}>
        <h1 style={{ ...S.title, textAlign: "center", fontSize: 28, marginBottom: 16 }}>TZAAR</h1>
        <div style={{ ...S.card, textAlign: "center" }}>
          <div style={S.cardTitle}>Select Board Setup</div>
          {myIdx === 0 ? (
            <div style={{ display: "flex", gap: 12, justifyContent: "center" }}>
              <button style={bs(true)} onClick={() => submitAction({ kind: "set_setup", setup: "random" })}>Random</button>
              <button style={bs(false)} onClick={() => submitAction({ kind: "set_setup", setup: "fixed" })}>Fixed</button>
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
        <h1 style={{ ...S.title, textAlign: "center", fontSize: 28, marginBottom: 16 }}>TZAAR — Game Over</h1>
        <div style={{ ...S.card, textAlign: "center" }}>
          <div style={{ fontSize: 22, fontWeight: 700, marginBottom: 12, color: "#c9a84c" }}>
            {winnerPlayer ? `${winnerPlayer.name} wins!` : "Draw!"}
          </div>
          <div style={{ display: "flex", gap: 12, justifyContent: "center", marginBottom: 16 }}>
            <PlayerPanel player={me} isCurrent={false} isMe board={state.board} />
            <PlayerPanel player={opp} isCurrent={false} isMe={false} board={state.board} />
          </div>
        </div>
        <div style={S.card}><HexBoard state={state} submitAction={() => {}} actionMode="capture" /></div>
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
  return (
    <div style={S.app}>
      <div style={S.overlay} />
      <div style={S.content}>
        {/* Header */}
        <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 12 }}>
          <h1 style={{ ...S.title, fontSize: 24, margin: 0 }}>TZAAR</h1>
          <div style={{
            padding: "4px 12px", borderRadius: 12, fontSize: 12,
            background: isMyTurn ? "rgba(39,174,96,0.2)" : "rgba(255,255,255,0.05)",
            border: `1px solid ${isMyTurn ? "#27ae60" : "#30363d"}`,
            color: isMyTurn ? "#27ae60" : "#888",
          }}>
            {phaseInfo?.description || (isMyTurn ? "Your turn" : `${opp.name}'s turn`)}
          </div>
        </div>

        {/* Player panels */}
        <div style={{ display: "flex", gap: 12, marginBottom: 12 }}>
          <PlayerPanel player={me} isCurrent={state.current_player === myIdx} isMe board={state.board} />
          <PlayerPanel player={opp} isCurrent={state.current_player === oppIdx} isMe={false} board={state.board} />
        </div>

        {/* Action mode selector (second action only) */}
        {isMyTurn && subPhase === "second_action" && (
          <div style={{ ...S.card, display: "flex", gap: 12, justifyContent: "center", padding: 12 }}>
            <button style={bs(actionMode === "capture")} onClick={() => setActionMode("capture")}>Capture</button>
            <button style={bs(actionMode === "stack")} onClick={() => setActionMode("stack")}>Stack</button>
            {canPass && (
              <button style={{ ...S.btn, color: "#888", borderColor: "#555" }}
                onClick={() => submitAction({ kind: "pass" })}>Pass</button>
            )}
          </div>
        )}

        {/* Board */}
        <div style={S.card}>
          <HexBoard state={state} submitAction={submitAction} actionMode={subPhase === "second_action" ? actionMode : "capture"} />
        </div>

        {/* Hints */}
        <div style={{ ...S.card, display: "flex", gap: 12, justifyContent: "center", flexWrap: "wrap" }}>
          {isMyTurn && subPhase === "first_action" && (
            <div style={{ fontSize: 13, color: "#e67e22" }}>You must capture! Click one of your pieces, then click an opponent piece.</div>
          )}
          {isMyTurn && subPhase === "second_action" && (
            <div style={{ fontSize: 13, color: "#888" }}>
              {actionMode === "capture" ? "Select a piece to capture with, or switch to Stack/Pass" : "Select a piece to stack onto another of yours"}
            </div>
          )}
          {!isMyTurn && <div style={{ fontSize: 13, color: "#888" }}>Waiting for {opp.name}...</div>}
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
