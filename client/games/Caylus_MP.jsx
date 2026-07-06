import { useState, useRef, useCallback, useEffect } from "react";

import { WS_URL } from "../ws.js";

// ============================================================
// CONSTANTS (mirrored from server/caylus/state.py)
// ============================================================

const PLAYER_COLORS_LIST = [
  { key: "blue", bg: "#2563eb", light: "#93c5fd", name: "Blue" },
  { key: "red", bg: "#dc2626", light: "#fca5a5", name: "Red" },
  { key: "green", bg: "#16a34a", light: "#86efac", name: "Green" },
  { key: "orange", bg: "#ea580c", light: "#fdba74", name: "Orange" },
  { key: "black", bg: "#374151", light: "#9ca3af", name: "Black" },
];

// Resource cubes — correct board game colors
const RES = {
  food:  { color: "#e0599b", bg: "#e0599b22", label: "Food",  sym: "F" },  // pink
  wood:  { color: "#8B5E3C", bg: "#8B5E3C22", label: "Wood",  sym: "W" },  // brown
  stone: { color: "#7f8c8d", bg: "#7f8c8d22", label: "Stone", sym: "S" },  // grey
  cloth: { color: "#3b82f6", bg: "#3b82f622", label: "Cloth", sym: "C" },  // blue
  gold:  { color: "#d4a017", bg: "#d4a01722", label: "Gold",  sym: "G" },  // gold
};

// Building shorthand — use ∣ for OR choices to be very obvious
const BLDG_ICONS = {
  // Neutral & basic
  n_farm:      { short: null, icon: "🌾", cubeShort: [{food:2},"or",{cloth:1}] },
  n_sawmill:   { short: null, icon: "🪓", cubeShort: [{wood:1}] },
  n_quarry:    { short: null, icon: "⛏",  cubeShort: [{stone:1}] },
  n_carpenter: { short: "Build 🟫",  icon: "🔨" },
  n_market:    { short: "Sell→4$",   icon: "💰" },
  n_peddler:   { short: "Buy 1$→□",  icon: "🛒" },
  b_peddler:   { short: "Buy 1$→□",  icon: "🛒" },
  b_market:    { short: "Sell→4$",   icon: "💰" },
  b_goldmine:  { short: null, icon: "✦",  cubeShort: [{gold:1}] },
  // Wood buildings
  w_farm:      { short: null, icon: "🌾", cubeShort: [{food:2},"or",{cloth:1}] },
  w_sawmill:   { short: null, icon: "🪓", cubeShort: [{wood:2}] },
  w_quarry:    { short: null, icon: "⛏",  cubeShort: [{stone:2}] },
  w_market:    { short: "Sell→6$",   icon: "💰" },
  w_peddler:   { short: "Buy×2 2$",  icon: "🛒" },
  w_tailor:    { short: null, icon: "✂️",  cubeShort: [{cloth:1},"→","2VP"] },
  w_church:    { short: "2$→3VP",    icon: "⛪" },
  w_lawyer:    { short: "→🏠",       icon: "⚖️" },
  // Stone buildings
  s_farm:      { short: null, icon: "🌾", cubeShort: [{food:2},{cloth:1}] },
  s_sawmill:   { short: null, icon: "🪓", cubeShort: [{wood:2},{food:1}] },
  s_quarry:    { short: null, icon: "⛏",  cubeShort: [{stone:2},{food:1}] },
  s_market:    { short: "Sell→8$",   icon: "💰" },
  s_mason:     { short: "Build ⬜",  icon: "🧱" },
  s_architect: { short: "Build 🔵",  icon: "🏛" },
  s_bank:      { short: "$→G",       icon: "🏦" },
  s_alchemist: { short: "□□→G",      icon: "⚗️" },
  s_goldmine:  { short: null, icon: "✦",  cubeShort: [{gold:1}] },
  // Prestige — these are inactive buildings, just show VP
  p_statue:    { short: "7VP",  icon: "🗿" },
  p_theater:   { short: "8VP",  icon: "🎭" },
  p_university:{ short: "8VP",  icon: "📚" },
  p_monument:  { short: "10VP", icon: "🏛" },
  p_granary:   { short: "6VP",  icon: "🌾" },
  p_weaver:    { short: "6VP",  icon: "🧶" },
  p_cathedral: { short: "12VP", icon: "⛪" },
  p_library:   { short: "5VP",  icon: "📖" },
  p_hotel:     { short: "5VP",  icon: "🏨" },
  // Residential
  residential: { short: "🏠",   icon: "🏠" },
};

const SPECIAL_INFO = {
  gate:            { icon: "🚪", short: "Free move",  desc: "Move worker to any unoccupied road space for free" },
  trading_post:    { icon: "💰", short: "+3$",        desc: "Take 3 deniers from the stock" },
  merchants_guild: { icon: "📜", short: "Provost ±3", desc: "Move the provost 1–3 spaces in either direction" },
  joust_field:     { icon: "⚔️", short: "1$+C → ⭐",  desc: "Pay 1 denier + 1 cloth → gain 1 royal favor" },
  stables:         { icon: "🐎", short: "Reorder",    desc: "Change turn order (up to 3 workers)" },
  inn:             { icon: "🍺", short: "1$/worker",   desc: "Pay only 1 denier per worker next turn" },
};

const CASTLE_SECTIONS = {
  dungeon: { name: "Dungeon", capacity: 6, vpPerBatch: 5 },
  walls:   { name: "Walls",   capacity: 10, vpPerBatch: 4 },
  towers:  { name: "Towers",  capacity: 14, vpPerBatch: 3 },
};

// Favor track levels: each level has a rich display (cubes) + text fallback + hover description
const FAVOR_TRACKS = {
  prestige: {
    name: "Prestige", icon: "⭐",
    levels: [
      { cubes: null, text: "1VP",  desc: "Gain 1 victory point" },
      { cubes: null, text: "2VP",  desc: "Gain 2 victory points" },
      { cubes: null, text: "3VP",  desc: "Gain 3 victory points" },
      { cubes: null, text: "4VP",  desc: "Gain 4 victory points" },
      { cubes: null, text: "5VP",  desc: "Gain 5 victory points" },
    ],
  },
  deniers: {
    name: "Deniers", icon: "💰",
    levels: [
      { cubes: null, text: "3$",  desc: "Gain 3 deniers" },
      { cubes: null, text: "4$",  desc: "Gain 4 deniers" },
      { cubes: null, text: "5$",  desc: "Gain 5 deniers" },
      { cubes: null, text: "6$",  desc: "Gain 6 deniers" },
      { cubes: null, text: "7$",  desc: "Gain 7 deniers" },
    ],
  },
  resources: {
    name: "Resources", icon: "📦",
    levels: [
      { cubes: [{food:1}],                 text: null, desc: "Gain 1 food" },
      { cubes: [{wood:1},"or",{stone:1}],  text: null, desc: "Gain 1 wood OR 1 stone (your choice)" },
      { cubes: [{cloth:1}],                text: null, desc: "Gain 1 cloth" },
      { cubes: ["□","→","□□"],             text: null, desc: "Swap: pay 1 resource → gain 2 of a different type" },
      { cubes: [{gold:1}],                 text: null, desc: "Gain 1 gold" },
    ],
  },
  buildings: {
    name: "Buildings", icon: "🏗️",
    levels: [
      { cubes: null, text: "—",       desc: "No effect" },
      { cubes: null, text: "🔨−1",    desc: "Build a wood (brown) building with 1 fewer resource" },
      { cubes: null, text: "🧱−1",    desc: "Build a stone (grey) building with 1 fewer resource" },
      { cubes: null, text: "⚖️",      desc: "Use the Lawyer effect: transform a building to residential" },
      { cubes: null, text: "🏛−1",    desc: "Build a prestige (blue) building with 1 fewer resource" },
    ],
  },
};

const PHASES = [
  { id: "income", name: "1. Income" },
  { id: "workers", name: "2. Workers" },
  { id: "special", name: "3. Special" },
  { id: "provost", name: "4. Provost" },
  { id: "activate", name: "5. Activate" },
  { id: "castle", name: "6. Castle" },
  { id: "end_turn", name: "7. End Turn" },
];

const TC = {
  neutral:     { bg: "#f5d0c5", border: "#d4a089" },
  basic:       { bg: "#f5d0c5", border: "#d4a089" },
  wood:        { bg: "#d4a574", border: "#92400e" },
  stone:       { bg: "#b8b8b8", border: "#6b7280" },
  prestige:    { bg: "#93c5fd", border: "#2563eb" },
  residential: { bg: "#86efac", border: "#16a34a" },
  empty:       { bg: "#e8dcc8", border: "#c4b59a" },
};

// ============================================================
// RESOURCE CUBE RENDERING HELPERS
// ============================================================

function Cube({ type, size = 12 }) {
  const r = RES[type];
  if (!r) return null;
  return (
    <span title={r.label} style={{
      display: "inline-block", width: size, height: size,
      background: r.color, borderRadius: 2,
      border: `1px solid ${r.color}88`,
      boxShadow: "inset 1px 1px 0 rgba(255,255,255,0.35)",
    }} />
  );
}

/** Render a cubeShort array: [{food:2},"or",{cloth:1}] → pink pink | blue */
function CubeShortDisplay({ items, size = 8 }) {
  if (!items) return null;
  return (
    <span style={{ display: "inline-flex", gap: 1, alignItems: "center", flexWrap: "wrap", justifyContent: "center" }}>
      {items.map((item, idx) => {
        if (item === "or") return <span key={idx} style={{ fontSize: 7, color: "#78350f", fontWeight: 800 }}>∣</span>;
        if (item === "→") return <span key={idx} style={{ fontSize: 7, color: "#78350f" }}>→</span>;
        if (typeof item === "string") return <span key={idx} style={{ fontSize: 7, color: "#78350f", fontWeight: 700 }}>{item}</span>;
        // It's a resource dict like {food: 2}
        return Object.entries(item).map(([r, ct]) => (
          <span key={`${idx}_${r}`} style={{ display: "inline-flex", gap: 0, alignItems: "center" }}>
            {Array.from({ length: ct }).map((_, i) => <Cube key={i} type={r} size={size} />)}
          </span>
        ));
      })}
    </span>
  );
}

function CostDisplay({ cost, style: xs }) {
  if (!cost || Object.keys(cost).length === 0) return null;
  return (
    <span style={{ display: "inline-flex", gap: 1, alignItems: "center", ...xs }}>
      {Object.entries(cost).map(([r, ct]) => (
        <span key={r} style={{ display: "inline-flex", alignItems: "center", gap: 0 }}>
          {Array.from({ length: ct }).map((_, i) => <Cube key={i} type={r} size={9} />)}
        </span>
      ))}
    </span>
  );
}

function ResourceBadge({ type, count, small }) {
  const r = RES[type];
  if (!r) return null;
  return (
    <span style={{
      display: "inline-flex", alignItems: "center", gap: 2,
      background: r.bg, border: `1px solid ${r.color}55`,
      borderRadius: 4, padding: small ? "0 4px" : "1px 6px",
      fontSize: small ? 11 : 13, color: r.color, fontWeight: 600,
    }}>
      <Cube type={type} size={small ? 9 : 11} />
      {count > 0 && <span>{count}</span>}
    </span>
  );
}

// ============================================================
// WEBSOCKET CONNECTION HOOK
// ============================================================

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


  const createRoom = (name) => connect(() => send({ type: "create", game: "caylus", name }));
  const joinRoom = (code, name) => connect(() => send({ type: "join", room_code: code.toUpperCase(), name }));

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

// ============================================================
// SMALL UI COMPONENTS
// ============================================================

function PlayerToken({ color, size = 16 }) {
  if (!color) return null;
  return (
    <span style={{
      display: "inline-block", width: size, height: size, borderRadius: "50%",
      background: color.bg, border: `2px solid ${color.light}`,
      boxShadow: `0 1px 3px ${color.bg}66`, flexShrink: 0,
    }} />
  );
}

function WorkerCylinder({ color, size = 14 }) {
  if (!color) return null;
  return (
    <span style={{
      display: "inline-block", width: size, height: size * 1.3,
      borderRadius: `${size / 2}px ${size / 2}px 2px 2px`,
      background: `linear-gradient(135deg, ${color.light}, ${color.bg})`,
      border: `1.5px solid ${color.bg}`, flexShrink: 0,
    }} />
  );
}

function Btn({ children, onClick, disabled, variant = "primary", small, style: xs }) {
  const V = {
    primary:   { bg: "linear-gradient(135deg,#92400e,#78350f)", color: "#fef3c7", border: "1px solid #78350f" },
    secondary: { bg: "#fef3c7", color: "#78350f", border: "1px solid #d4a574" },
    danger:    { bg: "#fee2e2", color: "#dc2626", border: "1px solid #fca5a5" },
    success:   { bg: "#dcfce7", color: "#16a34a", border: "1px solid #86efac" },
  };
  const s = V[variant];
  return (
    <button
      onClick={onClick} disabled={disabled}
      style={{
        background: disabled ? "#e8dcc8" : s.bg, color: disabled ? "#a08060" : s.color,
        border: disabled ? "1px solid #c4b59a" : s.border, borderRadius: 6,
        padding: small ? "3px 8px" : "6px 14px", fontSize: small ? 11 : 13,
        fontWeight: 700, cursor: disabled ? "not-allowed" : "pointer",
        opacity: disabled ? 0.6 : 1, fontFamily: "inherit", ...xs,
      }}
    >{children}</button>
  );
}

function Tooltip({ text, children }) {
  const [show, setShow] = useState(false);
  const [pos, setPos] = useState("above");
  const ref = useRef(null);

  const onEnter = () => {
    setShow(true);
    if (ref.current) {
      const rect = ref.current.getBoundingClientRect();
      setPos(rect.top < 80 ? "below" : "above");
    }
  };

  return (
    <span
      ref={ref}
      onMouseEnter={onEnter}
      onMouseLeave={() => setShow(false)}
      style={{ position: "relative", display: "inline-flex" }}
    >
      {children}
      {show && text && (
        <span style={{
          position: "absolute",
          ...(pos === "above"
            ? { bottom: "calc(100% + 6px)" }
            : { top: "calc(100% + 6px)" }),
          left: "50%", transform: "translateX(-50%)",
          background: "#1e1208", color: "#fef3c7", padding: "5px 9px", borderRadius: 5,
          fontSize: 11, whiteSpace: "pre-line", zIndex: 100, pointerEvents: "none",
          boxShadow: "0 3px 10px rgba(0,0,0,0.3)", maxWidth: 280,
          lineHeight: 1.4, textAlign: "left",
        }}>{text}</span>
      )}
    </span>
  );
}

// ============================================================
// LOBBY
// ============================================================

function Lobby({ game }) {
  const [name, setName] = useState("");
  const [joinCode, setJoinCode] = useState("");
  const [mode, setMode] = useState(null);

  return (
    <div style={{
      minHeight: "100vh",
      background: "linear-gradient(160deg,#faf5eb 0%,#f0e6d2 50%,#e8dcc8 100%)",
      fontFamily: "'Crimson Text','Georgia',serif", color: "#3d2a14",
    }}>
      <div style={{ maxWidth: 500, margin: "0 auto", padding: "40px 16px", textAlign: "center" }}>
        <div style={{ fontSize: 48, marginBottom: 8 }}>🏰</div>
        <h1 style={{
          fontFamily: "'Cinzel','Palatino Linotype',serif", fontSize: 40, color: "#78350f",
          margin: "0 0 4px", textShadow: "0 2px 4px rgba(120,80,40,0.15)",
        }}>CAYLUS</h1>
        <p style={{ fontSize: 15, color: "#92400e", fontStyle: "italic", margin: "0 0 28px" }}>
          Build the King's castle and earn his favor
        </p>
        {!game.roomCode ? (
          <div style={{ background: "#faf5eb", border: "2px solid #d4a574", borderRadius: 12, padding: 20 }}>
            <input value={name} onChange={(e) => setName(e.target.value)} placeholder="Your name" maxLength={20}
              style={{ width: "100%", padding: "10px 14px", borderRadius: 8, border: "2px solid #d4a574", background: "#fef3c7", fontSize: 15, fontFamily: "inherit", marginBottom: 12, color: "#78350f" }} />
            {!mode && (
              <div style={{ display: "flex", gap: 8 }}>
                <Btn onClick={() => setMode("create")} style={{ flex: 1 }}>Create Room</Btn>
                <Btn onClick={() => setMode("join")} variant="secondary" style={{ flex: 1 }}>Join Room</Btn>
              </div>
            )}
            {mode === "create" && <Btn onClick={() => name && game.createRoom(name)} disabled={!name} style={{ width: "100%" }}>Create Room</Btn>}
            {mode === "join" && (
              <div style={{ display: "flex", gap: 8 }}>
                <input value={joinCode} onChange={(e) => setJoinCode(e.target.value.toUpperCase())} placeholder="Room code" maxLength={6}
                  style={{ flex: 1, padding: "10px 14px", borderRadius: 8, border: "2px solid #d4a574", background: "#fef3c7", fontSize: 15, fontFamily: "inherit", color: "#78350f", textTransform: "uppercase", letterSpacing: 3, textAlign: "center" }} />
                <Btn onClick={() => name && joinCode && game.joinRoom(joinCode, name)} disabled={!name || !joinCode}>Join</Btn>
              </div>
            )}
            {game.error && <div style={{ color: "#dc2626", marginTop: 8, fontSize: 13 }}>{game.error}</div>}
          </div>
        ) : (
          <div style={{ background: "#faf5eb", border: "2px solid #d4a574", borderRadius: 12, padding: 20 }}>
            <div style={{ fontSize: 12, color: "#92400e", marginBottom: 4 }}>ROOM CODE</div>
            <div style={{ fontSize: 32, fontWeight: 800, color: "#78350f", letterSpacing: 6, marginBottom: 16, fontFamily: "'Cinzel',serif" }}>{game.roomCode}</div>
            <div style={{ marginBottom: 16 }}>
              <div style={{ fontSize: 12, fontWeight: 600, color: "#92400e88", marginBottom: 6 }}>PLAYERS</div>
              {game.lobby.map((p) => (
                <div key={p.id} style={{ display: "flex", alignItems: "center", gap: 8, padding: "4px 8px", background: "#fef3c7", borderRadius: 6, marginBottom: 4, border: p.id === game.playerId ? "2px solid #92400e" : "1px solid #d4a574" }}>
                  <span style={{ fontWeight: 700, color: "#78350f", flex: 1 }}>{p.name}</span>
                  {p.is_host && <span style={{ fontSize: 10, background: "#92400e", color: "#fef3c7", borderRadius: 4, padding: "1px 6px" }}>HOST</span>}
                </div>
              ))}
            </div>
            {game.isHost && game.lobby.length >= 2 && <Btn onClick={game.startGame} style={{ width: "100%" }}>Start Game ({game.lobby.length} players)</Btn>}
            {game.isHost && game.lobby.length < 2 && <div style={{ fontSize: 13, color: "#92400e" }}>Waiting for players... (need at least 2)</div>}
            {!game.isHost && <div style={{ fontSize: 13, color: "#92400e" }}>Waiting for host to start...</div>}
          </div>
        )}
      </div>
    </div>
  );
}

// ============================================================
// TURN ORDER BAR — shows all players, resources, whose turn
// ============================================================

function TurnOrderBar({ gs, activeIdx, myIdx }) {
  const turnOrder = gs.turn_order || [];
  const orderedPlayers = turnOrder.map(idx => gs.players.find(p => p.index === idx)).filter(Boolean);
  gs.players.forEach(p => {
    if (!orderedPlayers.find(op => op.index === p.index)) orderedPlayers.push(p);
  });

  return (
    <div style={{
      background: "#faf5eb", border: "1px solid #d4a57466", borderRadius: 10,
      padding: "6px 10px", display: "flex", gap: 6, flexWrap: "wrap", alignItems: "stretch",
    }}>
      <div style={{ fontSize: 10, fontWeight: 700, color: "#92400e88", letterSpacing: 1, display: "flex", alignItems: "center", marginRight: 4 }}>
        TURN ORDER
      </div>
      {orderedPlayers.map((pl) => {
        const c = pl.color;
        const isActive = pl.index === activeIdx;
        const isYou = pl.index === myIdx;
        const avail = pl.workers_total - pl.workers_placed;

        return (
          <div key={pl.index} style={{
            background: isActive ? `${c.bg}18` : "transparent",
            border: isActive ? `2px solid ${c.bg}` : isYou ? `2px solid ${c.bg}55` : "1px solid #d4a57433",
            borderRadius: 8, padding: "4px 8px", display: "flex", flexDirection: "column", gap: 2,
            minWidth: 130, flex: "1 1 130px",
            boxShadow: isActive ? `0 0 8px ${c.bg}22` : "none",
            position: "relative",
          }}>
            {isActive && (
              <div style={{
                position: "absolute", top: -1, right: 6, background: c.bg, color: "#fff",
                fontSize: 8, fontWeight: 800, padding: "1px 5px", borderRadius: "0 0 4px 4px", letterSpacing: 0.5,
              }}>ACTIVE</div>
            )}
            <div style={{ display: "flex", alignItems: "center", gap: 4 }}>
              <PlayerToken color={c} size={14} />
              <span style={{ fontWeight: 700, fontSize: 12, color: c.bg }}>{pl.name}</span>
              {isYou && <span style={{ fontSize: 8, background: "#dbeafe", color: "#2563eb", borderRadius: 3, padding: "0 3px", fontWeight: 700 }}>YOU</span>}
              {pl.passed && <span style={{ fontSize: 8, background: "#fee2e2", color: "#dc2626", borderRadius: 3, padding: "0 3px", fontWeight: 700 }}>PASS</span>}
              <span style={{ marginLeft: "auto", fontWeight: 800, fontSize: 13, color: "#78350f" }}>{pl.score}VP</span>
            </div>
            <div style={{ display: "flex", alignItems: "center", gap: 3, flexWrap: "wrap" }}>
              <span style={{ fontSize: 11, fontWeight: 700, color: "#b8860b", background: "#fef3c7", borderRadius: 3, padding: "0 3px" }}>💰{pl.deniers}</span>
              {Object.entries(pl.resources).map(([r, ct]) => (
                ct > 0 ? (
                  <span key={r} style={{ display: "inline-flex", alignItems: "center", gap: 1, fontSize: 10, fontWeight: 600, color: RES[r]?.color }}>
                    <Cube type={r} size={8} />{ct}
                  </span>
                ) : null
              ))}
              <span style={{ fontSize: 9, color: "#78350f88", marginLeft: "auto", display: "inline-flex", alignItems: "center", gap: 1 }}>
                {Array.from({ length: avail }).map((_, i) => <WorkerCylinder key={i} color={c} size={6} />)}
                {avail === 0 && <span>—</span>}
              </span>
            </div>
          </div>
        );
      })}
    </div>
  );
}

// ============================================================
// PHASE TRACKER
// ============================================================

function PhaseTracker({ currentPhase }) {
  return (
    <div style={{ display: "flex", gap: 2, flexWrap: "wrap" }}>
      {PHASES.map((ph, i) => (
        <div key={ph.id} style={{
          padding: "2px 6px", borderRadius: 4, fontSize: 10, fontWeight: 700,
          background: i === currentPhase ? "#92400e" : "#fef3c7",
          color: i === currentPhase ? "#fef3c7" : "#92400e88",
          border: i === currentPhase ? "1px solid #78350f" : "1px solid #d4a57444",
        }}>{ph.name}</div>
      ))}
    </div>
  );
}

// ============================================================
// BUILDING TILE — reusable for road & stock
// ============================================================

function BuildingTile({ building, width = 64, showCost = false, tooltip, onClick, highlight, badges, children }) {
  if (!building) return null;
  const bType = building.type || "neutral";
  const tc = TC[bType] || TC.empty;
  const bInfo = BLDG_ICONS[building.id] || BLDG_ICONS[bType] || {};

  const tooltipText = tooltip || (
    `${building.name}${building.description ? "\n" + building.description : ""}` +
    `${building.cost ? "\nCost: " + Object.entries(building.cost).map(([r, n]) => `${n} ${r}`).join(", ") : ""}` +
    `${building.vp ? ` · ${building.vp} VP` : ""}`
  );

  return (
    <Tooltip text={tooltipText}>
      <div
        onClick={onClick}
        style={{
          width, minHeight: 60, borderRadius: 6,
          background: tc.bg,
          border: highlight ? "2px solid #16a34a" : `1px solid ${tc.border}`,
          padding: 3, fontSize: 9, textAlign: "center",
          cursor: onClick ? "pointer" : "default",
          boxShadow: highlight ? "0 0 6px #16a34a44" : "none",
          transform: highlight ? "translateY(-2px)" : "none",
          transition: "transform 0.1s, box-shadow 0.1s",
          position: "relative", display: "flex", flexDirection: "column",
          alignItems: "center", gap: 1,
        }}
      >
        {badges}
        {bInfo.icon && <div style={{ fontSize: 12, lineHeight: 1 }}>{bInfo.icon}</div>}
        <div style={{ fontWeight: 700, color: "#3d2a14", lineHeight: 1.1, fontSize: 8 }}>{building.name}</div>
        {/* Cube-based shorthand for production buildings */}
        {bInfo.cubeShort && <CubeShortDisplay items={bInfo.cubeShort} size={7} />}
        {/* Text shorthand for other buildings */}
        {!bInfo.cubeShort && bInfo.short && (
          <div style={{ fontSize: 7, color: "#5c3a1e", fontWeight: 600, background: "rgba(255,255,255,0.5)", borderRadius: 2, padding: "0 2px" }}>
            {bInfo.short}
          </div>
        )}
        {showCost && building.cost && <CostDisplay cost={building.cost} />}
        {showCost && building.vp && <div style={{ fontSize: 7, color: "#78350f", fontWeight: 700 }}>{building.vp}VP</div>}
        {children}
      </div>
    </Tooltip>
  );
}

// ============================================================
// INTERACTIVE ROAD — clickable buildings in track order
// ============================================================

function RoadPanel({ gs, validPlaceActions, onPlaceWorker }) {
  const placeableMap = {};
  if (validPlaceActions) {
    validPlaceActions.forEach(a => { placeableMap[a.road_index] = a; });
  }

  return (
    <div style={{ background: "rgba(120,80,40,0.06)", borderRadius: 10, padding: 10, border: "1px solid #d4a57444" }}>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 6 }}>
        <span style={{ fontSize: 12, fontWeight: 700, color: "#78350f", letterSpacing: 1 }}>🛤️ ROAD</span>
        <div style={{ display: "flex", gap: 8, alignItems: "center", fontSize: 10 }}>
          <span style={{ color: "#dc2626", fontWeight: 700 }}>▼ Bailiff: {gs.bailiff_position + 1}</span>
          <span style={{ color: "#2563eb", fontWeight: 700 }}>▲ Provost: {gs.provost_position + 1}</span>
        </div>
      </div>

      <div style={{ display: "flex", gap: 3, flexWrap: "wrap" }}>
        {gs.road.map((slot, i) => {
          const b = slot.building;
          const bType = b ? (b.type || "neutral") : "empty";
          const tc = TC[bType] || TC.empty;
          const isBailiff = i === gs.bailiff_position;
          const isProvost = i === gs.provost_position;
          const beyondProvost = i > gs.provost_position;
          const ownerColor = (slot.house != null) ? gs.players.find(p => p.index === slot.house)?.color : null;
          const workerColor = (slot.worker != null) ? gs.players.find(p => p.index === slot.worker)?.color : null;
          const canPlace = !!placeableMap[i];
          const action = placeableMap[i];
          const bInfo = b ? (BLDG_ICONS[b.id] || BLDG_ICONS[bType] || {}) : {};

          const tooltipText = b
            ? `${b.name}${b.description ? "\n" + b.description : ""}${action ? `\n💰 Place worker: ${action.cost}$` : ""}`
            : "Empty slot";

          return (
            <Tooltip key={i} text={tooltipText}>
              <div
                onClick={() => canPlace && onPlaceWorker(action)}
                style={{
                  width: 64, minHeight: 70, borderRadius: 6,
                  background: beyondProvost ? "#e8dcc855" : tc.bg,
                  border: canPlace ? "2px solid #16a34a"
                    : isBailiff ? "2px solid #dc2626"
                    : isProvost ? "2px solid #2563eb"
                    : `1px solid ${tc.border}`,
                  padding: 3, fontSize: 9, textAlign: "center",
                  opacity: beyondProvost ? 0.4 : 1, position: "relative",
                  cursor: canPlace ? "pointer" : "default",
                  transition: "transform 0.1s, box-shadow 0.1s",
                  boxShadow: canPlace ? "0 0 6px #16a34a44" : "none",
                  transform: canPlace ? "translateY(-2px)" : "none",
                  display: "flex", flexDirection: "column", alignItems: "center", gap: 1,
                }}
              >
                {b && bInfo.icon && <div style={{ fontSize: 13, lineHeight: 1 }}>{bInfo.icon}</div>}
                <div style={{ fontWeight: 700, color: "#3d2a14", lineHeight: 1.1, fontSize: b ? 8 : 9, minHeight: 10 }}>
                  {b ? b.name : "—"}
                </div>
                {b && bInfo.cubeShort && <CubeShortDisplay items={bInfo.cubeShort} size={7} />}
                {b && !bInfo.cubeShort && bInfo.short && (
                  <div style={{ fontSize: 7, color: "#5c3a1e", fontWeight: 600, background: "rgba(255,255,255,0.5)", borderRadius: 2, padding: "0 2px" }}>
                    {bInfo.short}
                  </div>
                )}
                <div style={{ fontSize: 7, color: "#78350f66", marginTop: "auto" }}>{i + 1}</div>

                {ownerColor && (
                  <div style={{ position: "absolute", bottom: 1, left: 2, width: 8, height: 8, borderRadius: "50%", background: ownerColor.bg, border: `1px solid ${ownerColor.light}` }} />
                )}
                {workerColor && (
                  <div style={{ position: "absolute", top: 1, right: 1 }}>
                    <WorkerCylinder color={workerColor} size={8} />
                  </div>
                )}
                {(isBailiff || isProvost) && (
                  <div style={{ position: "absolute", bottom: -2, right: 1, fontSize: 7, fontWeight: 800, display: "flex", gap: 1 }}>
                    {isBailiff && <span style={{ color: "#dc2626" }}>B</span>}
                    {isProvost && <span style={{ color: "#2563eb" }}>P</span>}
                  </div>
                )}
                {canPlace && (
                  <div style={{
                    position: "absolute", top: -4, left: "50%", transform: "translateX(-50%)",
                    background: "#16a34a", color: "#fff", fontSize: 7, fontWeight: 800,
                    padding: "0 4px", borderRadius: 3,
                  }}>{action.cost}$</div>
                )}
              </div>
            </Tooltip>
          );
        })}
      </div>
    </div>
  );
}

// ============================================================
// SPECIAL BUILDINGS — interactive, clickable on the board
// ============================================================

function SpecialBuildingsPanel({ gs, validSpecialActions, onPlaceSpecial }) {
  const specials = ["gate", "trading_post", "merchants_guild", "joust_field", "stables", "inn"];
  const ss = gs.special_state;

  // Build lookup: special_id → action
  const actionMap = {};
  if (validSpecialActions) {
    validSpecialActions.forEach(a => { actionMap[a.special_id] = a; });
  }

  return (
    <div style={{ background: "rgba(120,80,40,0.06)", borderRadius: 10, padding: 10, border: "1px solid #d4a57444" }}>
      <div style={{ fontSize: 12, fontWeight: 700, color: "#78350f", marginBottom: 4, letterSpacing: 1 }}>
        SPECIAL BUILDINGS
      </div>
      <div style={{ display: "flex", gap: 6, flexWrap: "wrap" }}>
        {specials.map(spId => {
          const info = SPECIAL_INFO[spId];
          const canPlace = !!actionMap[spId];
          const action = actionMap[spId];

          let occupants = [];
          if (spId === "stables") {
            occupants = (ss.stables || []).filter(x => x !== null).map(idx => gs.players.find(p => p.index === idx)?.color);
          } else if (spId === "inn") {
            if (ss.inn?.left != null) occupants.push(gs.players.find(p => p.index === ss.inn.left)?.color);
            if (ss.inn?.right != null) occupants.push(gs.players.find(p => p.index === ss.inn.right)?.color);
          } else {
            const w = ss[spId]?.worker;
            if (w != null) occupants.push(gs.players.find(p => p.index === w)?.color);
          }

          return (
            <Tooltip key={spId} text={info?.desc}>
              <div
                onClick={() => canPlace && onPlaceSpecial(action)}
                style={{
                  background: canPlace ? "#dcfce7" : "#fef3c7",
                  border: canPlace ? "2px solid #16a34a" : "1px solid #d4a574",
                  borderRadius: 6, padding: "5px 10px", fontSize: 11, fontWeight: 600,
                  color: "#78350f", display: "flex", alignItems: "center", gap: 5,
                  cursor: canPlace ? "pointer" : "default",
                  boxShadow: canPlace ? "0 0 6px #16a34a44" : "none",
                  transform: canPlace ? "translateY(-1px)" : "none",
                  transition: "transform 0.1s, box-shadow 0.1s",
                  position: "relative",
                }}
              >
                <span style={{ fontSize: 14 }}>{info?.icon}</span>
                <div>
                  <div style={{ fontSize: 11, fontWeight: 700 }}>{info?.short}</div>
                </div>
                {occupants.length > 0 && (
                  <div style={{ display: "flex", gap: 2 }}>
                    {occupants.map((c, i) => c && <WorkerCylinder key={i} color={c} size={9} />)}
                  </div>
                )}
                {occupants.length === 0 && !canPlace && <span style={{ color: "#c4b59a", fontSize: 9 }}>empty</span>}
                {canPlace && (
                  <span style={{
                    position: "absolute", top: -4, right: 4,
                    background: "#16a34a", color: "#fff", fontSize: 7, fontWeight: 800,
                    padding: "0 4px", borderRadius: 3,
                  }}>{action.cost || action.description?.match(/(\d+)\$/)?.[1] || ""}$</span>
                )}
              </div>
            </Tooltip>
          );
        })}
      </div>
    </div>
  );
}

// ============================================================
// ACTION PANEL — streamlined, board does the selection
// ============================================================

function ActionPanel({ game, gs, submitAction }) {
  const actions = gs.valid_actions || [];
  const yourTurn = game.yourTurn;
  const myIdx = gs.your_player_idx;
  const myPlayer = gs.players.find(p => p.index === myIdx);
  const myColor = myPlayer?.color;

  if (gs.game_over) {
    const sorted = [...gs.players].sort((a, b) => b.score - a.score);
    return (
      <div style={{ background: "#fef3c7", border: "2px solid #d97706", borderRadius: 10, padding: 16, textAlign: "center" }}>
        <div style={{ fontSize: 24, fontWeight: 800, color: "#78350f" }}>Game Over!</div>
        {sorted.map((pl, i) => (
          <div key={pl.index} style={{
            display: "flex", alignItems: "center", gap: 8, justifyContent: "center",
            padding: 3, fontWeight: i === 0 ? 800 : 400, fontSize: i === 0 ? 18 : 14, color: pl.color.bg,
          }}>
            <PlayerToken color={pl.color} size={i === 0 ? 22 : 16} />
            {pl.name}: {pl.score}VP {i === 0 && "👑"}
          </div>
        ))}
      </div>
    );
  }

  if (!yourTurn || actions.length === 0) {
    const waitNames = (game.waitingFor || []).map(pid => {
      const wp = gs.players.find(p => p.player_id === pid);
      return wp ? wp.name : pid;
    });
    return (
      <div style={{ background: "#faf5eb", border: "1px solid #d4a574", borderRadius: 10, padding: 12, textAlign: "center", color: "#92400e" }}>
        Waiting for {waitNames.join(", ") || "other players"}...
      </div>
    );
  }

  // Road and special placement are now handled by clicking on the board
  const roadActions = actions.filter(a => a.kind === "place_worker");
  const specialActions = actions.filter(a => a.kind === "place_special");
  const castleAction = actions.find(a => a.kind === "place_castle");
  const passAction = actions.find(a => a.kind === "pass");
  const incomeAction = actions.find(a => a.kind === "collect_income");
  const otherActions = actions.filter(a =>
    !["place_worker", "place_special", "place_castle", "pass", "collect_income"].includes(a.kind)
  );

  const hasPlacement = roadActions.length > 0 || specialActions.length > 0;
  const borderColor = myColor ? myColor.bg : "#d4a574";

  return (
    <div style={{
      background: myColor ? `${myColor.bg}10` : "#faf5eb",
      border: `2px solid ${borderColor}`, borderRadius: 10, padding: 10,
    }}>
      <div style={{ display: "flex", alignItems: "center", gap: 8, marginBottom: 6, flexWrap: "wrap" }}>
        {myColor && <PlayerToken color={myColor} size={20} />}
        <span style={{ fontWeight: 800, fontSize: 15, color: borderColor }}>Your Turn</span>
        {game.phaseInfo && <span style={{ fontSize: 11, color: "#78350f", fontStyle: "italic" }}>{game.phaseInfo.description}</span>}
      </div>

      {incomeAction && (
        <Btn onClick={() => submitAction({ kind: "collect_income" })}>Collect Income for All Players</Btn>
      )}

      {hasPlacement && (
        <div style={{
          fontSize: 11, color: "#16a34a", fontWeight: 600, marginBottom: 4,
          padding: "3px 8px", background: "#dcfce7", borderRadius: 5, display: "inline-block",
        }}>
          ↓ Click a green-highlighted space below to place your worker
        </div>
      )}

      {castleAction && (
        <div style={{ marginBottom: 6 }}>
          <Btn onClick={() => submitAction(castleAction)} small variant="secondary">🏰 {castleAction.description}</Btn>
        </div>
      )}

      {otherActions.length > 0 && (
        <div style={{ display: "flex", gap: 4, flexWrap: "wrap", marginBottom: 6 }}>
          {otherActions.map((a, i) => (
            <Btn
              key={`${a.kind}_${a.choice_id || a.track || a.delta || a.res1 || i}`}
              onClick={() => submitAction(a)} small
              variant={a.kind === "castle_skip" || a.choice_id === "skip" ? "danger" : "success"}
            >{a.description || a.label || a.kind}</Btn>
          ))}
        </div>
      )}

      {passAction && (
        <Btn onClick={() => submitAction({ kind: "pass" })} variant="danger" small>Pass</Btn>
      )}

      {game.error && <div style={{ color: "#dc2626", fontSize: 12, marginTop: 4 }}>{game.error}</div>}
    </div>
  );
}

// ============================================================
// CASTLE PANEL — with proper tooltip
// ============================================================

function CastlePanel({ gs }) {
  const secs = [
    { key: "dungeon", ...CASTLE_SECTIONS.dungeon, parts: gs.castle.dungeon, counted: gs.castle.dungeon_counted },
    { key: "walls", ...CASTLE_SECTIONS.walls, parts: gs.castle.walls, counted: gs.castle.walls_counted },
    { key: "towers", ...CASTLE_SECTIONS.towers, parts: gs.castle.towers, counted: gs.castle.towers_counted },
  ];

  const castleTooltip = "Castle Phase: Each player with a worker here may contribute batches.\nEach batch costs 1 food + 2 different cubes (wood/stone/cloth/gold).\nEach batch fills 1 slot in the current section and earns VP.\nMajority contributor gets a royal favor; last place loses 2 VP.";

  return (
    <div style={{ background: "rgba(120,80,40,0.06)", borderRadius: 10, padding: 10, border: "1px solid #d4a57444" }}>
      <div style={{ display: "flex", alignItems: "center", gap: 6, marginBottom: 6 }}>
        <Tooltip text={castleTooltip}>
          <span style={{ fontSize: 12, fontWeight: 700, color: "#78350f", letterSpacing: 1, cursor: "help" }}>
            🏰 CASTLE
          </span>
        </Tooltip>
        <span style={{ fontWeight: 400, fontSize: 10, color: "#92400e88", display: "inline-flex", alignItems: "center", gap: 2 }}>
          Batch = 1<Cube type="food" size={8} /> + 2 different cubes (
          <Cube type="wood" size={7} />
          <Cube type="stone" size={7} />
          <Cube type="cloth" size={7} />
          <Cube type="gold" size={7} />
          )
        </span>
      </div>
      <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
        {secs.map(s => {
          const isCurrent = s.key === gs.castle.current_section;
          const sectionTooltip = `${s.name}: ${s.parts.filter(x => x !== null).length}/${s.capacity} filled\n+${s.vpPerBatch} VP per batch contributed\n${isCurrent ? "⬅ Currently active section" : ""}${s.counted ? "\n✓ Already counted for this section" : ""}`;
          return (
            <Tooltip key={s.key} text={sectionTooltip}>
              <div style={{
                background: isCurrent ? "#fef3c7" : "#f5ead6",
                border: isCurrent ? "2px solid #d97706" : "1px solid #d4a57444",
                borderRadius: 8, padding: 6, minWidth: 100,
              }}>
                <div style={{ fontWeight: 700, fontSize: 11, color: "#78350f", marginBottom: 3, textAlign: "center" }}>
                  {s.name} ({s.parts.filter(x => x !== null).length}/{s.capacity})
                  {s.counted && <span style={{ color: "#16a34a", marginLeft: 3 }}>✓</span>}
                </div>
                <div style={{ display: "flex", flexWrap: "wrap", gap: 2, justifyContent: "center" }}>
                  {s.parts.map((pt, i) => {
                    const c = pt !== null ? gs.players.find(p => p.index === pt)?.color : null;
                    return (
                      <div key={i} style={{
                        width: 14, height: 14, borderRadius: 3,
                        border: c ? "none" : "1px dashed #c4995a55",
                        background: c ? c.bg : "transparent",
                      }} />
                    );
                  })}
                </div>
                <div style={{ fontSize: 9, color: "#92400e88", textAlign: "center", marginTop: 2 }}>+{s.vpPerBatch}VP/batch</div>
              </div>
            </Tooltip>
          );
        })}
      </div>
      {gs.castle.workers.length > 0 && (
        <div style={{ marginTop: 4, fontSize: 10, color: "#78350f", display: "flex", gap: 3, alignItems: "center" }}>
          Workers: {gs.castle.workers.map((w, i) => {
            const c = gs.players.find(p => p.index === w)?.color;
            return <WorkerCylinder key={i} color={c} size={9} />;
          })}
        </div>
      )}
    </div>
  );
}

// ============================================================
// FAVOR TABLE — clearer descriptions with tooltips
// ============================================================

function FavorTablePanel({ gs }) {
  const fca = gs.favor_columns_available;
  return (
    <div style={{ background: "rgba(120,80,40,0.06)", borderRadius: 10, padding: 10, border: "1px solid #d4a57444", flex: "1 1 340px" }}>
      <div style={{ fontSize: 12, fontWeight: 700, color: "#78350f", marginBottom: 6 }}>⭐ ROYAL FAVORS</div>
      <table style={{ borderCollapse: "collapse", fontSize: 10, width: "100%" }}>
        <thead>
          <tr>
            <th style={{ textAlign: "left", padding: "3px 4px", color: "#78350f", borderBottom: "1px solid #d4a57444" }}>Track</th>
            {[1, 2, 3, 4, 5].map(n => (
              <th key={n} style={{
                padding: "3px 4px", color: "#78350f", textAlign: "center",
                opacity: n <= fca ? 1 : 0.2,
                background: n <= fca ? "#fef3c7" : "transparent", borderRadius: 3,
                borderBottom: "1px solid #d4a57444",
              }}>{n}</th>
            ))}
          </tr>
        </thead>
        <tbody>
          {Object.entries(FAVOR_TRACKS).map(([k, t]) => (
            <tr key={k}>
              <td style={{ padding: "4px 4px", fontWeight: 600, color: "#78350f", borderBottom: "1px solid #d4a57422" }}>
                {t.icon} {t.name}
              </td>
              {t.levels.map((lvl, i) => (
                <td key={i} style={{
                  padding: "3px 4px", textAlign: "center",
                  opacity: i < fca ? 1 : 0.2, color: "#5c3a1e",
                  borderBottom: "1px solid #d4a57422",
                }}>
                  <Tooltip text={lvl.desc}>
                    <div>
                      <div style={{ fontWeight: 600, display: "flex", alignItems: "center", justifyContent: "center", gap: 1, minHeight: 16 }}>
                        {lvl.cubes
                          ? <CubeShortDisplay items={lvl.cubes} size={9} />
                          : <span>{lvl.text}</span>
                        }
                      </div>
                      <div style={{ display: "flex", gap: 1, justifyContent: "center", marginTop: 1 }}>
                        {gs.players.filter(p => p.favors[k] === i + 1).map(p => (
                          <div key={p.index} style={{
                            width: 8, height: 8, borderRadius: "50%",
                            background: p.color.bg, border: `1px solid ${p.color.light}`,
                          }} />
                        ))}
                      </div>
                    </div>
                  </Tooltip>
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

// ============================================================
// BUILDING STOCK — with tooltips, costs as cubes
// ============================================================

function BuildingStockPanel({ buildingStock }) {
  return (
    <div style={{ background: "rgba(120,80,40,0.06)", borderRadius: 10, padding: 10, border: "1px solid #d4a57444", flex: "1 1 340px" }}>
      <div style={{ fontSize: 12, fontWeight: 700, color: "#78350f", marginBottom: 6 }}>🏗️ BUILDING STOCK</div>
      <div style={{ display: "flex", gap: 10, flexWrap: "wrap" }}>
        {Object.entries(buildingStock).map(([type, buildings]) => (
          <div key={type}>
            <div style={{ fontSize: 10, fontWeight: 700, color: (TC[type] || TC.empty).border, marginBottom: 3, textTransform: "capitalize" }}>
              {type} ({buildings.length})
            </div>
            <div style={{ display: "flex", gap: 3, flexWrap: "wrap", maxWidth: 360 }}>
              {buildings.map(b => (
                <BuildingTile key={b.id} building={b} width={62} showCost />
              ))}
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}

// ============================================================
// GAME LOG
// ============================================================

function GameLog({ logs }) {
  const ref = useRef(null);
  useEffect(() => {
    if (ref.current) ref.current.scrollTop = ref.current.scrollHeight;
  }, [logs]);

  return (
    <div style={{ background: "rgba(120,80,40,0.06)", borderRadius: 10, padding: 10, border: "1px solid #d4a57444" }}>
      <div style={{ fontSize: 12, fontWeight: 700, color: "#78350f", marginBottom: 4 }}>📜 LOG</div>
      <div ref={ref} style={{ maxHeight: 180, overflowY: "auto", fontSize: 11, color: "#5c3a1e", lineHeight: 1.4 }}>
        {logs.map((e, i) => (
          <div key={i} style={{
            borderBottom: "1px solid #d4a57411", padding: "1px 0",
            fontWeight: e.startsWith("Turn") || e.startsWith("FINAL") || e.startsWith("Counting") ? 700 : 400,
            color: e.startsWith("Turn") ? "#78350f" : e.startsWith("—") ? "#92400e" : "#5c3a1e",
          }}>{e}</div>
        ))}
      </div>
    </div>
  );
}

// ============================================================
// PASSING SCALE
// ============================================================

function PassingScale({ gs }) {
  return (
    <div style={{ display: "flex", gap: 3, alignItems: "center", padding: "2px 8px", fontSize: 11, color: "#78350f" }}>
      <span style={{ fontWeight: 700, marginRight: 3 }}>Pass order:</span>
      {gs.passing_scale.map((ps, i) => {
        const c = ps !== null ? gs.players.find(p => p.index === ps)?.color : null;
        return (
          <div key={i} style={{
            width: 24, height: 24, borderRadius: 5,
            border: c ? `2px solid ${c.bg}` : "1.5px dashed #c4995a44",
            background: c ? `${c.bg}22` : "transparent",
            display: "flex", alignItems: "center", justifyContent: "center",
            fontSize: 10, fontWeight: 700, color: "#92400e",
          }}>
            {c ? <PlayerToken color={c} size={12} /> : i + 1}
          </div>
        );
      })}
    </div>
  );
}

// ============================================================
// MAIN GAME BOARD
// ============================================================

function GameBoard({ game }) {
  const gs = game.gameState;
  if (!gs) return <div style={{ textAlign: "center", padding: 40, color: "#78350f" }}>Loading game state...</div>;

  const myIdx = gs.your_player_idx;
  const actions = gs.valid_actions || [];

  // Determine active player
  let activeIdx = null;
  if (gs.pending_favors) activeIdx = gs.pending_favors.queue[gs.pending_favors.queue_index]?.player_idx;
  else if (gs.pending_gate) activeIdx = gs.pending_gate.player_idx;
  else if (gs.pending_provost) activeIdx = gs.pending_provost.player_idx;
  else if (gs.pending_activation) activeIdx = gs.pending_activation.worker_idx;
  else if (gs.pending_castle) activeIdx = gs.pending_castle.player_idx;
  else if (gs.pending_owner_bonus) activeIdx = gs.pending_owner_bonus.owner_idx;
  else if (gs.pending_inn) activeIdx = gs.pending_inn.player_idx;
  else activeIdx = gs.current_player_idx;

  // Split actions for board interactivity
  const roadPlaceActions = actions.filter(a => a.kind === "place_worker");
  const specialPlaceActions = actions.filter(a => a.kind === "place_special");

  return (
    <div style={{
      minHeight: "100vh",
      background: "linear-gradient(160deg,#faf5eb 0%,#f0e6d2 50%,#e8dcc8 100%)",
      fontFamily: "'Crimson Text','Georgia',serif", color: "#3d2a14",
    }}>
      <style>{`@import url('https://fonts.googleapis.com/css2?family=Cinzel:wght@400;600;800&family=Crimson+Text:ital,wght@0,400;0,600;0,700;1,400&display=swap');*{box-sizing:border-box;}::-webkit-scrollbar{height:5px;width:5px;}::-webkit-scrollbar-track{background:transparent;}::-webkit-scrollbar-thumb{background:#c4995a55;border-radius:3px;}`}</style>
      <div style={{ maxWidth: 1100, margin: "0 auto", padding: "10px 10px" }}>
        <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
          {/* Header */}
          <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", flexWrap: "wrap", gap: 6 }}>
            <div>
              <h1 style={{ fontFamily: "'Cinzel','Palatino Linotype',serif", fontSize: 22, color: "#78350f", margin: 0 }}>🏰 CAYLUS</h1>
              <span style={{ fontSize: 11, color: "#92400e88" }}>Turn {gs.turn} | Room: {game.roomCode}</span>
            </div>
            <PhaseTracker currentPhase={gs.current_phase} />
          </div>

          {/* Turn order bar */}
          <TurnOrderBar gs={gs} activeIdx={activeIdx} myIdx={myIdx} />

          {/* Action panel */}
          <ActionPanel game={game} gs={gs} submitAction={game.submitAction} />

          {/* Passing scale */}
          <PassingScale gs={gs} />

          {/* Special buildings — now interactive */}
          <SpecialBuildingsPanel
            gs={gs}
            validSpecialActions={game.yourTurn ? specialPlaceActions : []}
            onPlaceSpecial={(action) => game.submitAction(action)}
          />

          {/* Interactive Road */}
          <RoadPanel
            gs={gs}
            validPlaceActions={game.yourTurn ? roadPlaceActions : []}
            onPlaceWorker={(action) => game.submitAction(action)}
          />

          {/* Castle */}
          <CastlePanel gs={gs} />

          {/* Favor table + Building stock */}
          <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
            <FavorTablePanel gs={gs} />
            <BuildingStockPanel buildingStock={gs.building_stock} />
          </div>

          {/* Game log */}
          <GameLog logs={game.gameLogs} />
        </div>
      </div>
    </div>
  );
}

// ============================================================
// APP
// ============================================================

export default function App() {
  const game = useGameConnection();
  if (!game.gameStarted) return <Lobby game={game} />;
  return <GameBoard game={game} />;
}
