import React, { useState, useEffect, useRef, useCallback, useMemo } from "react";

import { WS_URL } from "../ws.js";

const AXIAL_DIRS = [[1,0],[-1,0],[0,1],[0,-1],[1,-1],[-1,1]];
const COLOR_HEX = {
  ivory: "#f5e6c8", blue: "#2980b9", red: "#c0392b",
  green: "#27ae60", black: "#2c3e50", joker: "#ccc",
};
const COLOR_STROKE = {
  ivory: "#c9a84c", blue: "#1a5276", red: "#8e2a20",
  green: "#1e8449", black: "#1a252f", joker: "#888",
};
const HEX_SIZE = 24;
const SQRT3 = Math.sqrt(3);

function hexKey(q, r) { return `${q},${r}`; }
function parseHex(key) { return key.split(",").map(Number); }
function isValid(q, r) { return Math.abs(q) <= 3 && Math.abs(r) <= 4 && Math.abs(q + r) <= 3; }
function hexToPixel(q, r) { return { x: HEX_SIZE * 1.5 * q, y: HEX_SIZE * (SQRT3/2*q + SQRT3*r) }; }

function allPositions() {
  const p = [];
  for (let q = -3; q <= 3; q++) for (let r = -4; r <= 4; r++) if (isValid(q, r)) p.push([q, r]);
  return p;
}
const ALL_POSITIONS = allPositions();

function gridLines() {
  const lines = [], s = new Set(ALL_POSITIONS.map(([q,r]) => hexKey(q,r)));
  for (const [q, r] of ALL_POSITIONS) for (const [dq, dr] of [[1,0],[0,1],[1,-1]]) {
    if (s.has(hexKey(q+dq, r+dr))) { const f = hexToPixel(q, r), t = hexToPixel(q+dq, r+dr); lines.push({x1:f.x,y1:f.y,x2:t.x,y2:t.y}); }
  }
  return lines;
}
const GRID_LINES = gridLines();

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
function bs(p, d) { return { ...S.btn, ...(p ? S.btnP : {}), ...(d ? S.dis : {}) }; }
function getMyPlayerIdx(state) { if (!state?.players) return 0; const i = state.players.findIndex(p => p.player_id === state.your_player_id); return i >= 0 ? i : 0; }

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
  const wsRef = useRef(null), tokenRef = useRef(null);
  const send = useCallback((msg) => { if (wsRef.current?.readyState === WebSocket.OPEN) wsRef.current.send(JSON.stringify(msg)); }, []);
  const connect = useCallback((onOpen) => {
    if (wsRef.current?.readyState === WebSocket.OPEN) { onOpen?.(); return; }
    const ws = new WebSocket(WS_URL); wsRef.current = ws;
    ws.onopen = () => { setConnected(true); setError(null); if (tokenRef.current) ws.send(JSON.stringify({ type: "reconnect", token: tokenRef.current })); onOpen?.(); };
    ws.onmessage = (evt) => { const msg = JSON.parse(evt.data); switch (msg.type) {
      case "created": case "joined": setRoomCode(msg.room_code); setPlayerId(msg.player_id); setToken(msg.token); tokenRef.current = msg.token; sessionStorage.setItem("game_token", msg.token); ws.send(JSON.stringify({ type: "auth", token: msg.token })); break;
      case "authenticated": setRoomCode(msg.room_code); setPlayerId(msg.player_id); setIsHost(msg.is_host); setGameStarted(msg.game_started); break;
      case "lobby_update": setLobby(msg.players); if (msg.game_started !== undefined) setGameStarted(msg.game_started); break;
      case "game_started": setGameStarted(true); break;
      case "game_state": setGameState(msg.state); setPhaseInfo(msg.phase_info); setYourTurn(msg.your_turn); setWaitingFor(msg.waiting_for || []); break;
      case "game_log": setGameLogs(prev => [...prev, ...msg.messages]); break;
      case "game_over": setGameOver(true); break;
      case "action_error": setError(msg.message); break;
      case "error": setError(msg.message); break;
    }};
    ws.onclose = () => { setConnected(false); setTimeout(() => { if (tokenRef.current) connect(); }, 2000); };
  }, []);

  const createRoom = (name) => connect(() => send({ type: "create", game: "lyngk", name }));
  const joinRoom = (code, name) => connect(() => send({ type: "join", room_code: code.toUpperCase(), name }));
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

  return { connected, roomCode, playerId, token, isHost, lobby, gameStarted, gameState, phaseInfo, yourTurn, waitingFor, gameLogs, gameOver, error,
    createRoom, joinRoom, startGame, submitAction,
  };
}

function HexBoard({ state, submitAction }) {
  const board = state.board;
  const validActions = state.valid_actions || [];
  const isMyTurn = state.current_player === getMyPlayerIdx(state);
  const [selected, setSelected] = useState(null);

  useEffect(() => { setSelected(null); }, [state.current_player]);

  const moveSources = useMemo(() => new Set(validActions.filter(a => a.kind === "move").map(a => a.from)), [validActions]);
  const moveDests = useMemo(() => {
    if (!selected) return new Set();
    return new Set(validActions.filter(a => a.kind === "move" && a.from === selected).map(a => a.to));
  }, [selected, validActions]);

  const handleClick = useCallback((key) => {
    if (!isMyTurn) return;
    if (selected && moveDests.has(key)) { submitAction({ kind: "move", from: selected, to: key }); setSelected(null); return; }
    if (moveSources.has(key)) { setSelected(key === selected ? null : key); return; }
    setSelected(null);
  }, [isMyTurn, selected, moveDests, moveSources, submitAction]);

  const allPixels = ALL_POSITIONS.map(([q, r]) => hexToPixel(q, r));
  const pad = HEX_SIZE * 2;
  const minX = Math.min(...allPixels.map(p => p.x)) - pad, maxX = Math.max(...allPixels.map(p => p.x)) + pad;
  const minY = Math.min(...allPixels.map(p => p.y)) - pad, maxY = Math.max(...allPixels.map(p => p.y)) + pad;

  return (
    <svg viewBox={`${minX} ${minY} ${maxX-minX} ${maxY-minY}`} style={{ width: "100%", maxHeight: 420, display: "block" }}>
      {GRID_LINES.map((l, i) => <line key={i} x1={l.x1} y1={l.y1} x2={l.x2} y2={l.y2} stroke="#333" strokeWidth={1} />)}
      {ALL_POSITIONS.map(([q, r]) => {
        const key = hexKey(q, r);
        const stack = board[key] || [];
        const { x, y } = hexToPixel(q, r);
        const isDest = moveDests.has(key);
        const isSrc = moveSources.has(key);
        const isSel = key === selected;
        const top = stack.length > 0 ? stack[stack.length - 1] : null;
        const baseR = HEX_SIZE * 0.7;

        return (
          <g key={key} onClick={() => handleClick(key)} style={{ cursor: (isDest || isSrc) ? "pointer" : "default" }}>
            {!top && !isDest && <circle cx={x} cy={y} r={3} fill="#444" />}
            {isDest && <circle cx={x} cy={y} r={baseR} fill="rgba(76,175,80,0.2)" stroke="#4caf50" strokeWidth={2} />}
            {isSel && <circle cx={x} cy={y} r={baseR + 3} fill="none" stroke="#ffeb3b" strokeWidth={3} />}
            {top && (
              <>
                <circle cx={x} cy={y} r={baseR} fill={COLOR_HEX[top]} stroke={COLOR_STROKE[top]} strokeWidth={2} />
                {stack.length > 1 && (
                  <text x={x} y={y + 1} textAnchor="middle" dominantBaseline="middle"
                    fill={top === "black" ? "#e8d5a3" : "#333"} fontSize={12} fontWeight={700} fontFamily="monospace">
                    {stack.length}
                  </text>
                )}
              </>
            )}
          </g>
        );
      })}
    </svg>
  );
}

function PlayerPanel({ player, isCurrent, isMe, claims, score }) {
  return (
    <div style={{ ...S.card, flex: 1, padding: 12, border: `2px solid ${isCurrent ? "#c9a84c" : "#30363d"}`, boxShadow: isCurrent ? "0 0 12px rgba(201,168,76,0.3)" : S.card.boxShadow }}>
      <div style={{ display: "flex", alignItems: "center", gap: 8, marginBottom: 6 }}>
        <span style={{ fontWeight: 700, color: isMe ? "#c9a84c" : "#e8d5a3", fontSize: 14 }}>{player.name} {isMe ? "(You)" : ""}</span>
      </div>
      <div style={{ display: "flex", gap: 8, fontSize: 12, color: "#888", flexWrap: "wrap" }}>
        <span style={{ color: "#c9a84c" }}>Score: {score}</span>
        {claims.length > 0 && claims.map(c => (
          <span key={c} style={{ padding: "1px 6px", borderRadius: 4, background: COLOR_HEX[c], color: c === "black" ? "#fff" : "#000", fontSize: 10 }}>{c}</span>
        ))}
      </div>
    </div>
  );
}

function Lobby({ game }) {
  const [name, setName] = useState(""); const [joinCode, setJoinCode] = useState(""); const [mode, setMode] = useState(null);
  if (!game.roomCode && !mode) return (
    <div style={S.app}><div style={S.overlay} /><div style={S.content}><div style={{ textAlign: "center", paddingTop: 60 }}>
      <h1 style={{ ...S.title, fontSize: 48, marginBottom: 8 }}>LYNGK</h1>
      <p style={{ color: "#888", fontSize: 14, marginBottom: 32 }}>A game of the GIPF project</p>
      <div style={{ display: "flex", gap: 12, justifyContent: "center" }}>
        <button style={bs(true)} onClick={() => setMode("create")}>Create Room</button>
        <button style={bs(false)} onClick={() => setMode("join")}>Join Room</button>
      </div>
    </div></div></div>
  );
  if (!game.roomCode) return (
    <div style={S.app}><div style={S.overlay} /><div style={S.content}><div style={{ textAlign: "center", paddingTop: 60 }}>
      <h1 style={{ ...S.title, marginBottom: 24 }}>LYNGK</h1>
      <div style={{ ...S.card, maxWidth: 360, margin: "0 auto" }}>
        <div style={S.cardTitle}>{mode === "create" ? "Create Room" : "Join Room"}</div>
        <div style={{ display: "flex", flexDirection: "column", gap: 12 }}>
          <input style={S.input} placeholder="Your name" value={name} onChange={e => setName(e.target.value)}
            onKeyDown={e => { if (e.key === "Enter" && name.trim()) { if (mode === "create") game.createRoom(name.trim()); else if (joinCode.trim()) game.joinRoom(joinCode.trim(), name.trim()); }}} />
          {mode === "join" && <input style={S.input} placeholder="Room code" value={joinCode} onChange={e => setJoinCode(e.target.value.toUpperCase())}
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
    </div></div></div>
  );
  return (
    <div style={S.app}><div style={S.overlay} /><div style={S.content}><div style={{ textAlign: "center", paddingTop: 60 }}>
      <h1 style={{ ...S.title, marginBottom: 24 }}>LYNGK</h1>
      <div style={{ ...S.card, maxWidth: 400, margin: "0 auto" }}>
        <div style={S.cardTitle}>Room: {game.roomCode}</div>
        <div style={{ marginBottom: 16 }}>
          {game.lobby.map((p, i) => (<div key={i} style={{ padding: "6px 12px", borderRadius: 6, marginBottom: 4, background: "rgba(255,255,255,0.05)", fontSize: 14, display: "flex", justifyContent: "space-between" }}><span>{p.name}</span><span style={{ color: "#888", fontSize: 12 }}>{p.is_host ? "Host" : "Player"}</span></div>))}
        </div>
        {game.isHost && game.lobby.length >= 2 && <button style={bs(true)} onClick={() => game.startGame()}>Start Game</button>}
        {game.isHost && game.lobby.length < 2 && <div style={{ color: "#888", fontSize: 13 }}>Waiting for another player...</div>}
        {!game.isHost && <div style={{ color: "#888", fontSize: 13 }}>Waiting for host to start...</div>}
      </div>
    </div></div></div>
  );
}

function GameBoard({ game }) {
  const { gameState: state, phaseInfo, gameLogs, submitAction } = game;
  const logRef = useRef(null);
  useEffect(() => { if (logRef.current) logRef.current.scrollTop = logRef.current.scrollHeight; }, [gameLogs]);

  if (!state || !state.players) return <div style={S.app}><div style={S.content}><div style={S.card}>Loading game...</div></div></div>;

  const myIdx = getMyPlayerIdx(state);
  const oppIdx = 1 - myIdx;
  const me = state.players[myIdx], opp = state.players[oppIdx];
  const isMyTurn = state.current_player === myIdx;
  const validActions = state.valid_actions || [];
  const claimActions = validActions.filter(a => a.kind === "claim_color");
  const canPass = validActions.some(a => a.kind === "pass");

  if (state.game_over) {
    const winnerPlayer = state.players.find(p => p.player_id === state.winner);
    return (
      <div style={S.app}><div style={S.overlay} /><div style={S.content}>
        <h1 style={{ ...S.title, textAlign: "center", fontSize: 28, marginBottom: 16 }}>LYNGK — Game Over</h1>
        <div style={{ ...S.card, textAlign: "center" }}>
          <div style={{ fontSize: 22, fontWeight: 700, marginBottom: 12, color: "#c9a84c" }}>{winnerPlayer ? `${winnerPlayer.name} wins!` : "Draw!"}</div>
          <div style={{ display: "flex", gap: 12, justifyContent: "center" }}>
            <PlayerPanel player={me} isCurrent={false} isMe claims={state.claims[me.color] || []} score={state.scores[myIdx]} />
            <PlayerPanel player={opp} isCurrent={false} isMe={false} claims={state.claims[opp.color] || []} score={state.scores[oppIdx]} />
          </div>
        </div>
        <div style={S.card}><HexBoard state={state} submitAction={() => {}} /></div>
      </div></div>
    );
  }

  return (
    <div style={S.app}>
      <div style={S.overlay} />
      <div style={S.content}>
        <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 12 }}>
          <h1 style={{ ...S.title, fontSize: 24, margin: 0 }}>LYNGK</h1>
          <div style={{ padding: "4px 12px", borderRadius: 12, fontSize: 12, background: isMyTurn ? "rgba(39,174,96,0.2)" : "rgba(255,255,255,0.05)", border: `1px solid ${isMyTurn ? "#27ae60" : "#30363d"}`, color: isMyTurn ? "#27ae60" : "#888" }}>
            {phaseInfo?.description || (isMyTurn ? "Your turn" : `${opp.name}'s turn`)}
          </div>
        </div>

        <div style={{ display: "flex", gap: 12, marginBottom: 12 }}>
          <PlayerPanel player={me} isCurrent={state.current_player === myIdx} isMe claims={state.claims[me.color] || []} score={state.scores[myIdx]} />
          <PlayerPanel player={opp} isCurrent={state.current_player === oppIdx} isMe={false} claims={state.claims[opp.color] || []} score={state.scores[oppIdx]} />
        </div>

        {/* Claim buttons */}
        {isMyTurn && claimActions.length > 0 && (
          <div style={{ ...S.card, padding: 12, display: "flex", gap: 8, justifyContent: "center", flexWrap: "wrap", alignItems: "center" }}>
            <span style={{ fontSize: 12, color: "#888" }}>Claim a color:</span>
            {claimActions.map(a => (
              <button key={a.color} style={{ ...S.btn, fontSize: 11, padding: "4px 12px", background: COLOR_HEX[a.color], color: a.color === "black" ? "#fff" : "#000", border: `1px solid ${COLOR_STROKE[a.color]}` }}
                onClick={() => submitAction(a)}>{a.color}</button>
            ))}
          </div>
        )}

        <div style={S.card}><HexBoard state={state} submitAction={submitAction} /></div>

        <div style={{ ...S.card, display: "flex", gap: 12, justifyContent: "center", flexWrap: "wrap" }}>
          {isMyTurn && !canPass && <div style={{ fontSize: 13, color: "#888" }}>Click a piece to select, then click a destination to move</div>}
          {canPass && <button style={{ ...S.btn, color: "#e74c3c", borderColor: "#e74c3c" }} onClick={() => submitAction({ kind: "pass" })}>Pass</button>}
          {!isMyTurn && <div style={{ fontSize: 13, color: "#888" }}>Waiting for {opp.name}...</div>}
        </div>

        {game.error && (<div style={{ ...S.card, textAlign: "center", padding: 12, border: "1px solid #e74c3c", background: "rgba(231,76,60,0.1)" }}><span style={{ fontSize: 13, color: "#e74c3c" }}>{game.error}</span></div>)}

        <div style={S.card}>
          <div style={S.cardTitle}>Game Log</div>
          <div ref={logRef} style={{ maxHeight: 150, overflowY: "auto", fontSize: 12, color: "#888" }}>
            {gameLogs.length === 0 ? <div style={{ fontStyle: "italic" }}>Game started...</div>
              : gameLogs.map((msg, i) => <div key={i} style={{ padding: "2px 0" }}>{msg}</div>)}
          </div>
        </div>
      </div>
    </div>
  );
}

export default function App() {
  const game = useGameConnection();
  if (!game.gameStarted) return <Lobby game={game} />;
  return <GameBoard game={game} />;
}
