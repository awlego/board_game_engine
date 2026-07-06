import React, { useState, useEffect, useRef, useCallback, useMemo } from "react";

// ─── CONFIGURATION ─────────────────────────────────────────────────
import { WS_URL } from "../ws.js";

// ─── CONSTANTS ─────────────────────────────────────────────────────

const BOARD_RADIUS = 3;
const AXIAL_DIRS = [[1,0],[-1,0],[0,1],[0,-1],[1,-1],[-1,1]];

const PIECE_STYLES = {
  white: { fill: "#e8e0d0", stroke: "#999", gipf: "#c9a84c" },
  black: { fill: "#2c3e50", stroke: "#1a252f", gipf: "#6a8caf" },
};

const HEX_SIZE = 30;
const SQRT3 = Math.sqrt(3);

// ─── HEX MATH ──────────────────────────────────────────────────────

function hexKey(q, r) { return `${q},${r}`; }
function parseHex(key) { const [q, r] = key.split(",").map(Number); return [q, r]; }
function hexDist(q, r) { return Math.max(Math.abs(q), Math.abs(r), Math.abs(q + r)); }
function isBoardSpot(q, r) { return hexDist(q, r) <= BOARD_RADIUS; }

function hexToPixel(q, r) {
  return { x: HEX_SIZE * 1.5 * q, y: HEX_SIZE * (SQRT3 / 2 * q + SQRT3 * r) };
}

function allBoardSpots() {
  const p = [];
  for (let q = -BOARD_RADIUS; q <= BOARD_RADIUS; q++)
    for (let r = -BOARD_RADIUS; r <= BOARD_RADIUS; r++)
      if (isBoardSpot(q, r)) p.push([q, r]);
  return p;
}
const ALL_SPOTS = allBoardSpots();

// Compute edge dots (same logic as server)
function computeEdgeDots() {
  const dots = [];
  const seen = new Set();
  for (const [q, r] of ALL_SPOTS) {
    for (const [dq, dr] of AXIAL_DIRS) {
      const oq = q + dq, or_ = r + dr;
      if (!isBoardSpot(oq, or_)) {
        const dk = hexKey(oq, or_);
        if (!seen.has(dk)) {
          seen.add(dk);
          dots.push({ key: dk, q: oq, r: or_, dq: -dq, dr: -dr, firstSpot: hexKey(q, r) });
        }
      }
    }
  }
  return dots;
}
const EDGE_DOTS = computeEdgeDots();

// Grid lines
function gridLines() {
  const lines = [];
  const s = new Set(ALL_SPOTS.map(([q,r]) => hexKey(q,r)));
  const dirs = [[1,0],[0,1],[1,-1]];
  for (const [q, r] of ALL_SPOTS) {
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


  const createRoom = (name) => { connect(() => send({ type: "create", game: "gipf", name })); };
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

// ─── HEX BOARD COMPONENT ──────────────────────────────────────────

function HexBoard({ state, submitAction, useGipf }) {
  const board = state.board;
  const validActions = state.valid_actions || [];
  const subPhase = state.sub_phase;
  const isMyTurn = useMemo(() => {
    const myIdx = getMyPlayerIdx(state);
    if (subPhase === "resolve_rows" && state.row_resolver !== null) return state.row_resolver === myIdx;
    return state.current_player === myIdx;
  }, [state, subPhase]);

  // Valid push dots
  const validDots = useMemo(() => {
    if (subPhase !== "push") return new Set();
    return new Set(
      validActions
        .filter(a => a.kind === "push" && a.is_gipf === useGipf)
        .map(a => a.dot)
    );
  }, [subPhase, validActions, useGipf]);

  // Valid resolve row actions
  const resolveActions = useMemo(() => {
    return validActions.filter(a => a.kind === "resolve_row");
  }, [validActions]);

  const rowHighlightKeys = useMemo(() => {
    if (subPhase !== "resolve_rows" || resolveActions.length === 0) return new Set();
    const keys = new Set();
    resolveActions.forEach(a => a.row_keys?.forEach(k => keys.add(k)));
    return keys;
  }, [subPhase, resolveActions]);

  const handleDotClick = useCallback((dotKey) => {
    if (!isMyTurn || subPhase !== "push") return;
    if (validDots.has(dotKey)) {
      submitAction({ kind: "push", dot: dotKey, is_gipf: useGipf });
    }
  }, [isMyTurn, subPhase, validDots, submitAction, useGipf]);

  const handleResolve = useCallback((action) => {
    if (!isMyTurn || subPhase !== "resolve_rows") return;
    submitAction(action);
  }, [isMyTurn, subPhase, submitAction]);

  // SVG viewBox
  const allPixels = [...ALL_SPOTS, ...EDGE_DOTS.map(d => [d.q, d.r])].map(([q, r]) => hexToPixel(q, r));
  const pad = HEX_SIZE + 10;
  const minX = Math.min(...allPixels.map(p => p.x)) - pad;
  const maxX = Math.max(...allPixels.map(p => p.x)) + pad;
  const minY = Math.min(...allPixels.map(p => p.y)) - pad;
  const maxY = Math.max(...allPixels.map(p => p.y)) + pad;
  const viewBox = `${minX} ${minY} ${maxX - minX} ${maxY - minY}`;

  const pieceR = HEX_SIZE * 0.55;

  return (
    <svg viewBox={viewBox} style={{ width: "100%", maxHeight: 480, display: "block" }}>
      {/* Grid lines */}
      {GRID_LINES.map((l, i) => <line key={i} x1={l.x1} y1={l.y1} x2={l.x2} y2={l.y2} stroke="#333" strokeWidth={1} />)}

      {/* Board spots */}
      {ALL_SPOTS.map(([q, r]) => {
        const key = hexKey(q, r);
        const piece = board[key];
        const { x, y } = hexToPixel(q, r);
        const inRow = rowHighlightKeys.has(key);

        return (
          <g key={key}>
            {/* Intersection dot */}
            {!piece && <circle cx={x} cy={y} r={4} fill="#555" />}
            {/* Row highlight */}
            {inRow && <circle cx={x} cy={y} r={pieceR + 5} fill="none" stroke="#f1c40f" strokeWidth={2.5} strokeDasharray="4 3" />}
            {/* Piece */}
            {piece && (
              <>
                <circle cx={x} cy={y} r={pieceR} fill={PIECE_STYLES[piece.color].fill}
                  stroke={piece.is_gipf ? PIECE_STYLES[piece.color].gipf : PIECE_STYLES[piece.color].stroke}
                  strokeWidth={piece.is_gipf ? 3 : 1.5} />
                {piece.is_gipf && (
                  <circle cx={x} cy={y} r={pieceR - 5} fill="none"
                    stroke={PIECE_STYLES[piece.color].gipf} strokeWidth={1.5} />
                )}
              </>
            )}
          </g>
        );
      })}

      {/* Edge dots */}
      {EDGE_DOTS.map(dot => {
        const { x, y } = hexToPixel(dot.q, dot.r);
        const isValid = validDots.has(dot.key);
        return (
          <g key={`dot-${dot.key}`}
            onClick={isValid ? () => handleDotClick(dot.key) : undefined}
            style={{ cursor: isValid ? "pointer" : "default" }}>
            <circle cx={x} cy={y} r={6}
              fill={isValid ? "rgba(76,175,80,0.4)" : "#333"}
              stroke={isValid ? "#4caf50" : "#444"}
              strokeWidth={isValid ? 2 : 1} />
          </g>
        );
      })}

      {/* Resolve row buttons (if applicable) */}
      {subPhase === "resolve_rows" && isMyTurn && resolveActions.length > 0 && (
        <g>
          {resolveActions.map((action, i) => {
            // Place a small "resolve" button at the center of the row
            const rowKeys = action.row_keys || [];
            const positions = rowKeys.map(k => { const [q,r] = parseHex(k); return hexToPixel(q,r); });
            const cx = positions.reduce((s, p) => s + p.x, 0) / positions.length;
            const cy = positions.reduce((s, p) => s + p.y, 0) / positions.length;
            const label = action.keep_gipf?.length > 0 ? "Keep G" : "Remove";
            return (
              <g key={i} onClick={() => handleResolve(action)} style={{ cursor: "pointer" }}>
                <rect x={cx - 28} y={cy - 10 + i * 24} width={56} height={18} rx={4}
                  fill="rgba(201,168,76,0.9)" stroke="#c9a84c" />
                <text x={cx} y={cy + i * 24} textAnchor="middle" dominantBaseline="middle"
                  fill="#0d1117" fontSize={9} fontWeight={700} fontFamily="sans-serif">{label}</text>
              </g>
            );
          })}
        </g>
      )}
    </svg>
  );
}

// ─── PLAYER PANEL ──────────────────────────────────────────────────

function PlayerPanel({ player, isCurrent, isMe, board, mode }) {
  const ps = PIECE_STYLES[player.color];
  const gipfCount = useMemo(() => {
    if (mode === "basic") return 0;
    return Object.values(board).filter(p => p && p.color === player.color && p.is_gipf).length;
  }, [board, player.color, mode]);

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
      <div style={{ display: "flex", gap: 16, fontSize: 12, color: "#888" }}>
        <span>Reserve: {player.reserve}</span>
        <span>Captured: {player.captured_opponent}</span>
        {mode !== "basic" && <span style={{ color: gipfCount > 0 ? "#c9a84c" : "#555" }}>GIPF: {gipfCount}</span>}
      </div>
    </div>
  );
}

// ─── LOBBY ─────────────────────────────────────────────────────────

function Lobby({ game }) {
  const [name, setName] = useState("");
  const [joinCode, setJoinCode] = useState("");
  const [mode, setMode] = useState(null);

  if (!game.roomCode && !mode) {
    return (
      <div style={S.app}><div style={S.overlay} /><div style={S.content}>
        <div style={{ textAlign: "center", paddingTop: 60 }}>
          <h1 style={{ ...S.title, fontSize: 48, marginBottom: 8 }}>GIPF</h1>
          <p style={{ color: "#888", fontSize: 14, marginBottom: 32 }}>The original game of the GIPF project</p>
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
          <h1 style={{ ...S.title, marginBottom: 24 }}>GIPF</h1>
          <div style={{ ...S.card, maxWidth: 360, margin: "0 auto" }}>
            <div style={S.cardTitle}>{mode === "create" ? "Create Room" : "Join Room"}</div>
            <div style={{ display: "flex", flexDirection: "column", gap: 12 }}>
              <input style={S.input} placeholder="Your name" value={name}
                onChange={e => setName(e.target.value)}
                onKeyDown={e => { if (e.key === "Enter" && name.trim()) { if (mode === "create") game.createRoom(name.trim()); else if (joinCode.trim()) game.joinRoom(joinCode.trim(), name.trim()); }}} />
              {mode === "join" && <input style={S.input} placeholder="Room code" value={joinCode}
                onChange={e => setJoinCode(e.target.value.toUpperCase())}
                onKeyDown={e => { if (e.key === "Enter" && name.trim() && joinCode.trim()) game.joinRoom(joinCode.trim(), name.trim()); }} />}
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
        <h1 style={{ ...S.title, marginBottom: 24 }}>GIPF</h1>
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

// ─── GAME BOARD ────────────────────────────────────────────────────

function GameBoard({ game }) {
  const { gameState: state, phaseInfo, gameLogs, submitAction } = game;
  const [useGipf, setUseGipf] = useState(false);
  const logRef = useRef(null);

  useEffect(() => { if (logRef.current) logRef.current.scrollTop = logRef.current.scrollHeight; }, [gameLogs]);
  useEffect(() => { setUseGipf(false); }, [state?.current_player, state?.sub_phase]);

  if (!state || !state.players) {
    return <div style={S.app}><div style={S.content}><div style={S.card}>Loading game...</div></div></div>;
  }

  const myIdx = getMyPlayerIdx(state);
  const oppIdx = 1 - myIdx;
  const me = state.players[myIdx];
  const opp = state.players[oppIdx];
  const phase = state.phase;
  const subPhase = state.sub_phase;
  const mode = state.mode;

  const isMyAction = useMemo(() => {
    if (subPhase === "resolve_rows" && state.row_resolver !== null) return state.row_resolver === myIdx;
    return state.current_player === myIdx;
  }, [state, myIdx, subPhase]);

  const canPlayGipf = mode !== "basic" && me.reserve >= 2 && (mode === "standard" || !me.has_played_single);

  // Config phase
  if (phase === "config") {
    return (
      <div style={S.app}><div style={S.overlay} /><div style={S.content}>
        <h1 style={{ ...S.title, textAlign: "center", fontSize: 28, marginBottom: 16 }}>GIPF</h1>
        <div style={{ ...S.card, textAlign: "center" }}>
          <div style={S.cardTitle}>Select Game Mode</div>
          {myIdx === 0 ? (
            <div style={{ display: "flex", gap: 12, justifyContent: "center", flexWrap: "wrap" }}>
              <button style={bs(true)} onClick={() => submitAction({ kind: "set_mode", mode: "basic" })}>Basic</button>
              <button style={bs(false)} onClick={() => submitAction({ kind: "set_mode", mode: "standard" })}>Standard</button>
              <button style={bs(false)} onClick={() => submitAction({ kind: "set_mode", mode: "tournament" })}>Tournament</button>
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
        <h1 style={{ ...S.title, textAlign: "center", fontSize: 28, marginBottom: 16 }}>GIPF — Game Over</h1>
        <div style={{ ...S.card, textAlign: "center" }}>
          <div style={{ fontSize: 22, fontWeight: 700, marginBottom: 12, color: "#c9a84c" }}>
            {winnerPlayer ? `${winnerPlayer.name} wins!` : "Draw!"}
          </div>
          <div style={{ display: "flex", gap: 12, justifyContent: "center", marginBottom: 16 }}>
            <PlayerPanel player={me} isCurrent={false} isMe board={state.board} mode={mode} />
            <PlayerPanel player={opp} isCurrent={false} isMe={false} board={state.board} mode={mode} />
          </div>
        </div>
        <div style={S.card}><HexBoard state={state} submitAction={() => {}} useGipf={false} /></div>
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
        <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 12 }}>
          <h1 style={{ ...S.title, fontSize: 24, margin: 0 }}>GIPF</h1>
          <div style={{ display: "flex", gap: 8, alignItems: "center" }}>
            <div style={{ padding: "4px 12px", borderRadius: 12, fontSize: 12, background: "rgba(255,255,255,0.05)", border: "1px solid #30363d", color: "#888" }}>
              {mode.charAt(0).toUpperCase() + mode.slice(1)}
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

        <div style={{ display: "flex", gap: 12, marginBottom: 12 }}>
          <PlayerPanel player={me} isCurrent={state.current_player === myIdx} isMe board={state.board} mode={mode} />
          <PlayerPanel player={opp} isCurrent={state.current_player === oppIdx} isMe={false} board={state.board} mode={mode} />
        </div>

        {/* GIPF toggle (Standard/Tournament) */}
        {isMyAction && subPhase === "push" && canPlayGipf && (
          <div style={{ ...S.card, display: "flex", gap: 12, justifyContent: "center", padding: 12 }}>
            <button style={bs(!useGipf)} onClick={() => setUseGipf(false)}>Single Piece</button>
            <button style={bs(useGipf)} onClick={() => setUseGipf(true)}>GIPF Piece (2x)</button>
          </div>
        )}

        <div style={S.card}>
          <HexBoard state={state} submitAction={submitAction} useGipf={useGipf} />
        </div>

        <div style={{ ...S.card, display: "flex", gap: 12, justifyContent: "center", flexWrap: "wrap" }}>
          {isMyAction && subPhase === "push" && (
            <div style={{ fontSize: 13, color: "#888" }}>Click a green edge dot to push your piece onto the board</div>
          )}
          {isMyAction && subPhase === "resolve_rows" && (
            <div style={{ fontSize: 13, color: "#c9a84c" }}>Click a resolve button on the board to handle the row of 4</div>
          )}
          {!isMyAction && <div style={{ fontSize: 13, color: "#888" }}>Waiting for {opp.name}...</div>}
        </div>

        {game.error && (
          <div style={{ ...S.card, textAlign: "center", padding: 12, border: "1px solid #e74c3c", background: "rgba(231,76,60,0.1)" }}>
            <span style={{ fontSize: 13, color: "#e74c3c" }}>{game.error}</span>
          </div>
        )}

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
