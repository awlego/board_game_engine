import React, { useState, useEffect, useRef, useCallback, useMemo } from "react";

// ─── CONFIGURATION ─────────────────────────────────────────────────
import { WS_URL } from "../ws.js";

// ─── CONSTANTS ─────────────────────────────────────────────────────

const BOARD_RADIUS = 8;
const CENTRAL_RADIUS = 2;
const EXCLUDED = new Set(["8,0","-8,0","0,8","0,-8","8,-8","-8,8"]);

const PIECE_COLORS = {
  white: { fill: "#e0d8c8", stroke: "#999", punct: "#c9a84c" },
  black: { fill: "#2c3e50", stroke: "#1a252f", punct: "#6a8caf" },
};

const HEX_SIZE = 12;  // Smaller for 211 positions
const SQRT3 = Math.sqrt(3);

// ─── HEX MATH ──────────────────────────────────────────────────────

function hexKey(q, r) { return `${q},${r}`; }
function parseHex(key) { const [q, r] = key.split(",").map(Number); return [q, r]; }
function hexDist(q, r) { return Math.max(Math.abs(q), Math.abs(r), Math.abs(q + r)); }
function isValid(q, r) { return hexDist(q, r) <= BOARD_RADIUS && !EXCLUDED.has(hexKey(q, r)); }
function isCentral(q, r) { return hexDist(q, r) <= CENTRAL_RADIUS; }

function hexToPixel(q, r) {
  return { x: HEX_SIZE * 1.5 * q, y: HEX_SIZE * (SQRT3 / 2 * q + SQRT3 * r) };
}

function allPositions() {
  const p = [];
  for (let q = -BOARD_RADIUS; q <= BOARD_RADIUS; q++)
    for (let r = -BOARD_RADIUS; r <= BOARD_RADIUS; r++)
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
      if (s.has(hexKey(q+dq, r+dr))) {
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
  content: { position: "relative", zIndex: 1, maxWidth: 1200, margin: "0 auto", padding: "16px 20px" },
  card: { background: "linear-gradient(135deg, rgba(22,27,34,0.95) 0%, rgba(13,17,23,0.98) 100%)", border: "1px solid #30363d", borderRadius: 8, padding: 20, marginBottom: 16, boxShadow: "0 4px 20px rgba(0,0,0,0.5)" },
  cardTitle: { fontFamily: font, fontSize: 18, color: "#c9a84c", marginBottom: 12, borderBottom: "1px solid #30363d", paddingBottom: 8 },
  btn: { fontFamily: font, fontSize: 14, padding: "8px 20px", borderRadius: 6, border: "1px solid #30363d", background: "linear-gradient(135deg, #21262d 0%, #161b22 100%)", color: "#e8d5a3", cursor: "pointer", transition: "all 0.2s", fontWeight: 600 },
  btnP: { background: "linear-gradient(135deg, #c9a84c 0%, #a08030 100%)", color: "#0d1117", border: "1px solid #c9a84c" },
  btnSm: { fontSize: 11, padding: "4px 10px" },
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


  const createRoom = (name) => { connect(() => send({ type: "create", game: "punct", name })); };
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

function HexBoard({ state, submitAction, selectedAction }) {
  const pieces = state.pieces || {};
  const validActions = state.valid_actions || [];
  const isMyTurn = state.current_player === getMyPlayerIdx(state);

  // Build a visibility map: cell -> top color
  const visMap = useMemo(() => {
    const vm = {};
    // Group by cell, find top level
    const cellPieces = {};
    Object.entries(pieces).forEach(([pid, piece]) => {
      const cells = [piece.punct_pos, ...piece.minor_positions];
      cells.forEach((c, ci) => {
        if (!cellPieces[c] || piece.level > cellPieces[c].level) {
          cellPieces[c] = { color: piece.color, level: piece.level, isPunct: ci === 0, pid };
        }
      });
    });
    return cellPieces;
  }, [pieces]);

  // Valid placement targets for the selected action
  const placementTargets = useMemo(() => {
    if (!selectedAction) return new Set();
    // Show PUNCT positions for matching actions
    return new Set(
      validActions
        .filter(a => {
          if (selectedAction.kind === "place") {
            return a.kind === "place" && a.rotation_idx === selectedAction.rotation_idx
              && a.piece_id.includes(selectedAction.shape);
          }
          return false;
        })
        .map(a => a.punct_pos)
    );
  }, [selectedAction, validActions]);

  // Moveable pieces
  const moveablePieces = useMemo(() => {
    return new Set(validActions.filter(a => a.kind === "move" || a.kind === "jump").map(a => a.piece_id));
  }, [validActions]);

  const handleCellClick = useCallback((key) => {
    if (!isMyTurn) return;

    if (selectedAction?.kind === "place" && placementTargets.has(key)) {
      const action = validActions.find(a =>
        a.kind === "place" &&
        a.punct_pos === key &&
        a.rotation_idx === selectedAction.rotation_idx &&
        a.piece_id.includes(selectedAction.shape)
      );
      if (action) submitAction(action);
      return;
    }
  }, [isMyTurn, selectedAction, placementTargets, validActions, submitAction]);

  // SVG viewBox
  const allPixels = ALL_POSITIONS.map(([q, r]) => hexToPixel(q, r));
  const pad = HEX_SIZE * 2;
  const minX = Math.min(...allPixels.map(p => p.x)) - pad;
  const maxX = Math.max(...allPixels.map(p => p.x)) + pad;
  const minY = Math.min(...allPixels.map(p => p.y)) - pad;
  const maxY = Math.max(...allPixels.map(p => p.y)) + pad;
  const viewBox = `${minX} ${minY} ${maxX - minX} ${maxY - minY}`;

  const dotR = HEX_SIZE * 0.45;

  return (
    <svg viewBox={viewBox} style={{ width: "100%", maxHeight: 500, display: "block" }}>
      {/* Grid lines */}
      {GRID_LINES.map((l, i) => <line key={i} x1={l.x1} y1={l.y1} x2={l.x2} y2={l.y2} stroke="#252525" strokeWidth={0.5} />)}

      {/* Central hex indicator */}
      {ALL_POSITIONS.filter(([q,r]) => isCentral(q,r)).map(([q,r]) => {
        const { x, y } = hexToPixel(q, r);
        return <circle key={`cen-${q},${r}`} cx={x} cy={y} r={dotR + 2} fill="rgba(201,168,76,0.05)" />;
      })}

      {/* Board dots */}
      {ALL_POSITIONS.map(([q, r]) => {
        const key = hexKey(q, r);
        const { x, y } = hexToPixel(q, r);
        const cellInfo = visMap[key];
        const isTarget = placementTargets.has(key);

        return (
          <g key={key} onClick={() => handleCellClick(key)}
            style={{ cursor: isTarget ? "pointer" : "default" }}>
            {/* Empty dot */}
            {!cellInfo && !isTarget && <circle cx={x} cy={y} r={2} fill="#444" />}
            {/* Target highlight */}
            {isTarget && <circle cx={x} cy={y} r={dotR} fill="rgba(76,175,80,0.3)" stroke="#4caf50" strokeWidth={1.5} />}
            {/* Piece dot */}
            {cellInfo && (
              <circle cx={x} cy={y} r={dotR}
                fill={PIECE_COLORS[cellInfo.color].fill}
                stroke={cellInfo.isPunct ? PIECE_COLORS[cellInfo.color].punct : PIECE_COLORS[cellInfo.color].stroke}
                strokeWidth={cellInfo.isPunct ? 2 : 1}
              />
            )}
          </g>
        );
      })}
    </svg>
  );
}

// ─── PIECE SELECTOR ────────────────────────────────────────────────

function PieceSelector({ reserve, color, selectedAction, setSelectedAction, validActions }) {
  const shapes = ["straight", "angular", "triangular"];

  const shapeRotations = { straight: 3, angular: 6, triangular: 6 };
  const [selectedRotation, setSelectedRotation] = useState(0);

  const shapeCounts = useMemo(() => {
    const counts = {};
    shapes.forEach(s => {
      counts[s] = reserve.filter(id => id.includes(s)).length;
    });
    return counts;
  }, [reserve]);

  const selectedShape = selectedAction?.shape || null;

  return (
    <div style={{ display: "flex", gap: 16, alignItems: "center", flexWrap: "wrap", justifyContent: "center" }}>
      {shapes.map(shape => {
        const count = shapeCounts[shape];
        const isSelected = selectedShape === shape;
        return (
          <div key={shape} style={{ textAlign: "center" }}>
            <button
              style={{
                ...S.btn, ...S.btnSm,
                ...(isSelected ? S.btnP : {}),
                opacity: count > 0 ? 1 : 0.3,
              }}
              disabled={count <= 0}
              onClick={() => {
                if (isSelected) {
                  setSelectedAction(null);
                } else {
                  setSelectedAction({ kind: "place", shape, rotation_idx: 0 });
                  setSelectedRotation(0);
                }
              }}
            >
              {shape} ({count})
            </button>
            {isSelected && (
              <div style={{ marginTop: 4, display: "flex", gap: 4, justifyContent: "center" }}>
                {Array.from({ length: shapeRotations[shape] }, (_, i) => (
                  <button key={i}
                    style={{
                      ...S.btn, ...S.btnSm, padding: "2px 6px", fontSize: 10,
                      ...(selectedAction?.rotation_idx === i ? S.btnP : {}),
                    }}
                    onClick={() => {
                      setSelectedRotation(i);
                      setSelectedAction({ kind: "place", shape, rotation_idx: i });
                    }}
                  >
                    R{i}
                  </button>
                ))}
              </div>
            )}
          </div>
        );
      })}
    </div>
  );
}

// ─── PLAYER PANEL ──────────────────────────────────────────────────

function PlayerPanel({ player, isCurrent, isMe, reserve }) {
  const ps = PIECE_COLORS[player.color];
  const count = reserve.length;

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
      <div style={{ fontSize: 12, color: "#888" }}>
        Reserve: {count} pieces
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
          <h1 style={{ ...S.title, fontSize: 48, marginBottom: 8 }}>PUNCT</h1>
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
          <h1 style={{ ...S.title, marginBottom: 24 }}>PUNCT</h1>
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
        <h1 style={{ ...S.title, marginBottom: 24 }}>PUNCT</h1>
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
  const [selectedAction, setSelectedAction] = useState(null);
  const logRef = useRef(null);

  useEffect(() => { if (logRef.current) logRef.current.scrollTop = logRef.current.scrollHeight; }, [gameLogs]);
  useEffect(() => { setSelectedAction(null); }, [state?.current_player]);

  if (!state || !state.players) {
    return <div style={S.app}><div style={S.content}><div style={S.card}>Loading game...</div></div></div>;
  }

  const myIdx = getMyPlayerIdx(state);
  const oppIdx = 1 - myIdx;
  const me = state.players[myIdx];
  const opp = state.players[oppIdx];
  const phase = state.phase;
  const isMyTurn = state.current_player === myIdx;
  const myColor = me.color;
  const myReserve = state.reserve?.[myColor] || [];
  const validActions = state.valid_actions || [];

  // Config phase
  if (phase === "config") {
    return (
      <div style={S.app}><div style={S.overlay} /><div style={S.content}>
        <h1 style={{ ...S.title, textAlign: "center", fontSize: 28, marginBottom: 16 }}>PUNCT</h1>
        <div style={{ ...S.card, textAlign: "center" }}>
          <div style={S.cardTitle}>Select Game Mode</div>
          {myIdx === 0 ? (
            <div style={{ display: "flex", gap: 12, justifyContent: "center" }}>
              <button style={bs(true)} onClick={() => submitAction({ kind: "set_mode", mode: "basic" })}>Basic</button>
              <button style={bs(false)} onClick={() => submitAction({ kind: "set_mode", mode: "standard" })}>Standard</button>
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
        <h1 style={{ ...S.title, textAlign: "center", fontSize: 28, marginBottom: 16 }}>PUNCT — Game Over</h1>
        <div style={{ ...S.card, textAlign: "center" }}>
          <div style={{ fontSize: 22, fontWeight: 700, marginBottom: 12, color: "#c9a84c" }}>
            {winnerPlayer ? `${winnerPlayer.name} wins!` : "Draw!"}
          </div>
        </div>
        <div style={S.card}><HexBoard state={state} submitAction={() => {}} selectedAction={null} /></div>
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
          <h1 style={{ ...S.title, fontSize: 24, margin: 0 }}>PUNCT</h1>
          <div style={{
            padding: "4px 12px", borderRadius: 12, fontSize: 12,
            background: isMyTurn ? "rgba(39,174,96,0.2)" : "rgba(255,255,255,0.05)",
            border: `1px solid ${isMyTurn ? "#27ae60" : "#30363d"}`,
            color: isMyTurn ? "#27ae60" : "#888",
          }}>
            {phaseInfo?.description || (isMyTurn ? "Your turn" : `${opp.name}'s turn`)}
          </div>
        </div>

        <div style={{ display: "flex", gap: 12, marginBottom: 12 }}>
          <PlayerPanel player={me} isCurrent={state.current_player === myIdx} isMe reserve={myReserve} />
          <PlayerPanel player={opp} isCurrent={state.current_player === oppIdx} isMe={false}
            reserve={state.reserve?.[opp.color] || []} />
        </div>

        {/* Piece selector for placement */}
        {isMyTurn && myReserve.length > 0 && (
          <div style={{ ...S.card, padding: 12 }}>
            <div style={{ textAlign: "center", fontSize: 12, color: "#888", marginBottom: 8 }}>
              Place a piece: select shape & rotation, then click board
            </div>
            <PieceSelector
              reserve={myReserve} color={myColor}
              selectedAction={selectedAction} setSelectedAction={setSelectedAction}
              validActions={validActions}
            />
          </div>
        )}

        <div style={S.card}>
          <HexBoard state={state} submitAction={submitAction} selectedAction={selectedAction} />
        </div>

        <div style={{ ...S.card, display: "flex", gap: 12, justifyContent: "center", flexWrap: "wrap" }}>
          {isMyTurn && !selectedAction && (
            <div style={{ fontSize: 13, color: "#888" }}>Select a shape above to place, or click a piece on the board to move/jump</div>
          )}
          {isMyTurn && selectedAction?.kind === "place" && (
            <div style={{ fontSize: 13, color: "#888" }}>Click a green-highlighted position on the board to place your {selectedAction.shape} piece</div>
          )}
          {!isMyTurn && <div style={{ fontSize: 13, color: "#888" }}>Waiting for {opp.name}...</div>}
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
