import { useState, useRef, useCallback, useEffect, useMemo } from "react";

import { WS_URL } from "../ws.js";
import CARD_CATALOG from "./agricola_cards.json";
import { AgricolaCard } from "./agricola_card.jsx";

// ============================================================
// CONSTANTS (mirrored from server/agricola/state.py)
// ============================================================

const ROWS = 3, COLS = 5, NUM_CELLS = 15;

const PLAYER_COLORS = [
  { key: "blue",   bg: "#2563eb", light: "#93c5fd", name: "Blue" },
  { key: "red",    bg: "#dc2626", light: "#fca5a5", name: "Red" },
  { key: "green",  bg: "#16a34a", light: "#86efac", name: "Green" },
  { key: "purple", bg: "#7c3aed", light: "#c4b5fd", name: "Purple" },
];

const GOODS = {
  food:      { icon: "🍲", label: "Food",      color: "#e0599b" },
  wood:      { icon: "🪵", label: "Wood",      color: "#8B5E3C" },
  clay:      { icon: "🧱", label: "Clay",      color: "#c2410c" },
  reed:      { icon: "🌿", label: "Reed",      color: "#0d9488" },
  stone:     { icon: "🪨", label: "Stone",     color: "#6b7280" },
  grain:     { icon: "🌾", label: "Grain",     color: "#ca8a04" },
  vegetable: { icon: "🥕", label: "Vegetable", color: "#ea580c" },
  sheep:     { icon: "🐑", label: "Sheep",     color: "#a8a29e" },
  boar:      { icon: "🐗", label: "Wild Boar", color: "#44403c" },
  cattle:    { icon: "🐄", label: "Cattle",    color: "#92400e" },
};

const ANIMALS = ["sheep", "boar", "cattle"];

const IMPROVEMENTS = {
  fireplace_2:      { name: "Fireplace",        cost: { clay: 2 }, points: 1, desc: "Cook: 🐑2 🐗2 🐄3 🥕2 · bake 2/grain" },
  fireplace_3:      { name: "Fireplace",        cost: { clay: 3 }, points: 1, desc: "Cook: 🐑2 🐗2 🐄3 🥕2 · bake 2/grain" },
  cooking_hearth_4: { name: "Cooking Hearth",   cost: { clay: 4 }, points: 1, desc: "Cook: 🐑2 🐗3 🐄4 🥕3 · bake 3/grain", upgrade: true },
  cooking_hearth_5: { name: "Cooking Hearth",   cost: { clay: 5 }, points: 1, desc: "Cook: 🐑2 🐗3 🐄4 🥕3 · bake 3/grain", upgrade: true },
  clay_oven:        { name: "Clay Oven",        cost: { clay: 3, stone: 1 }, points: 2, desc: "Bake: 1 grain → 5 food. Bake on build.", bakeLimit: 1, bakeValue: 5, oven: true },
  stone_oven:       { name: "Stone Oven",       cost: { clay: 1, stone: 3 }, points: 3, desc: "Bake: up to 2 grain → 4 each. Bake on build.", bakeLimit: 2, bakeValue: 4, oven: true },
  joinery:          { name: "Joinery",          cost: { wood: 2, stone: 2 }, points: 2, desc: "Harvest: 1 wood → 2 food. Scoring: 3/5/7 wood → 1/2/3 pts" },
  pottery:          { name: "Pottery",          cost: { clay: 2, stone: 2 }, points: 2, desc: "Harvest: 1 clay → 2 food. Scoring: 3/5/7 clay → 1/2/3 pts" },
  basketmaker:      { name: "Basketmaker's Workshop", cost: { reed: 2, stone: 2 }, points: 2, desc: "Harvest: 1 reed → 3 food. Scoring: 2/4/5 reed → 1/2/3 pts" },
  well:             { name: "Well",             cost: { wood: 1, stone: 3 }, points: 4, desc: "1 food on each of the next 5 round spaces" },
};

const FIREPLACES = ["fireplace_2", "fireplace_3"];
const HEARTHS = ["cooking_hearth_4", "cooking_hearth_5"];
const BAKE_VALUES = { fireplace_2: [null, 2], fireplace_3: [null, 2], cooking_hearth_4: [null, 3], cooking_hearth_5: [null, 3], clay_oven: [1, 5], stone_oven: [2, 4] };
const COOK_FIREPLACE = { sheep: 2, boar: 2, cattle: 3, vegetable: 2 };
const COOK_HEARTH = { sheep: 2, boar: 3, cattle: 4, vegetable: 3 };
const CRAFT_HARVEST = { joinery: ["wood", 2], pottery: ["clay", 2], basketmaker: ["reed", 3] };

const HARVEST_ROUNDS = [4, 7, 9, 11, 13, 14];

function inPlay(player) {
  return [...(player.occupations || []), ...(player.minors || [])];
}

function cardSpec(cid) {
  return CARD_CATALOG[cid] || { name: cid, text: "", cost: {} };
}

function bestCook(player) {
  let cook = null;
  const tables = [];
  for (const imp of player.improvements) {
    if (FIREPLACES.includes(imp)) tables.push(COOK_FIREPLACE);
    if (HEARTHS.includes(imp)) tables.push(COOK_HEARTH);
  }
  for (const inst of inPlay(player)) {
    if (cardSpec(inst.id).cook) tables.push(cardSpec(inst.id).cook);
  }
  for (const table of tables) {
    if (!cook) cook = { ...table };
    else for (const k of Object.keys(table)) cook[k] = Math.max(cook[k] || 0, table[k]);
  }
  return cook;
}

function rawValues(player) {
  const best = { grain: 1, vegetable: 1 };
  for (const inst of inPlay(player)) {
    const rv = cardSpec(inst.id).raw_values;
    if (rv) for (const k of Object.keys(rv)) best[k] = Math.max(best[k], rv[k]);
  }
  return best;
}

function houseCapacity(player) {
  let cap = 1, perRoom = false;
  for (const inst of inPlay(player)) {
    const hc = cardSpec(inst.id).house_capacity;
    if (hc === "per_room") perRoom = true;
    else if (typeof hc === "number") cap += hc;
  }
  if (perRoom) {
    const rooms = player.cells.filter((c) => c.type === "room").length;
    cap = Math.max(cap, rooms);
  }
  return cap;
}

function pastureBonus(player) {
  return inPlay(player).reduce(
    (sum, inst) => sum + (cardSpec(inst.id).pasture_capacity_bonus || 0), 0);
}

function hasLasso(player) {
  return inPlay(player).some((inst) => cardSpec(inst.id).lasso);
}

function costStr(cost) {
  const parts = Object.entries(cost || {}).map(([g, n]) => `${n}${GOODS[g].icon}`);
  return parts.length ? parts.join(" ") : "free";
}

// ── Farmyard geometry (mirror of state.py) ──────────────────

function cellEdges(idx) {
  const r = Math.floor(idx / COLS), c = idx % COLS;
  return [`h-${r}-${c}`, `h-${r + 1}-${c}`, `v-${r}-${c}`, `v-${r}-${c + 1}`];
}

function edgeCells(edge) {
  const [kind, rs, cs] = edge.split("-");
  const r = +rs, c = +cs;
  const cells = [];
  if (kind === "h") {
    if (r - 1 >= 0) cells.push((r - 1) * COLS + c);
    if (r <= ROWS - 1) cells.push(r * COLS + c);
  } else {
    if (c - 1 >= 0) cells.push(r * COLS + c - 1);
    if (c <= COLS - 1) cells.push(r * COLS + c);
  }
  return cells;
}

function sharedEdge(a, b) {
  const ra = Math.floor(a / COLS), ca = a % COLS;
  const rb = Math.floor(b / COLS), cb = b % COLS;
  if (ca === cb && Math.abs(ra - rb) === 1) return `h-${Math.max(ra, rb)}-${ca}`;
  if (ra === rb && Math.abs(ca - cb) === 1) return `v-${ra}-${Math.max(ca, cb)}`;
  return null;
}

function neighbors(idx) {
  const r = Math.floor(idx / COLS), c = idx % COLS;
  const out = [];
  if (r > 0) out.push(idx - COLS);
  if (r < ROWS - 1) out.push(idx + COLS);
  if (c > 0) out.push(idx - 1);
  if (c < COLS - 1) out.push(idx + 1);
  return out;
}

function computePastures(cells, fences) {
  const fenceSet = new Set(fences);
  const seen = new Set();
  const pastures = [];
  for (let start = 0; start < NUM_CELLS; start++) {
    if (seen.has(start)) continue;
    const region = [start];
    seen.add(start);
    const stack = [start];
    while (stack.length) {
      const cur = stack.pop();
      for (const nb of neighbors(cur)) {
        if (seen.has(nb) || fenceSet.has(sharedEdge(cur, nb))) continue;
        seen.add(nb);
        region.push(nb);
        stack.push(nb);
      }
    }
    let enclosed = true;
    for (const idx of region) {
      for (const e of cellEdges(idx)) {
        if (edgeCells(e).length === 1 && !fenceSet.has(e)) enclosed = false;
      }
    }
    if (enclosed && region.every((i) => cells[i].type === "empty")) {
      pastures.push(region.sort((a, b) => a - b));
    }
  }
  return pastures;
}

function pastureCapacity(cells, pasture) {
  const stables = pasture.filter((i) => cells[i].stable).length;
  return 2 * pasture.length * Math.pow(2, stables);
}

function animalTotals(player) {
  const totals = { sheep: 0, boar: 0, cattle: 0 };
  for (const c of player.cells) if (c.animal) totals[c.animal.type] += c.animal.count;
  if (player.pet) totals[player.pet] += 1;
  return totals;
}

// ============================================================
// WEBSOCKET CONNECTION HOOK
// ============================================================

function useGameConnection() {
  const [connected, setConnected] = useState(false);
  const [roomCode, setRoomCode] = useState(null);
  const [playerId, setPlayerId] = useState(null);
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
  // Set while an auto create/join/reconnect is in flight so the lobby can
  // show progress instead of an entry form: {kind, code?} or null.
  const [pendingIntent, setPendingIntent] = useState(null);

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
          setError(null);
          break;
        case "game_log":
          setGameLogs((prev) => [...prev, ...msg.messages]);
          break;
        case "game_over":
          setGameOver(true);
          break;
        case "action_error":
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

  const createRoom = (name, options) => connect(() =>
    send({ type: "create", game: "agricola", name, ...(options ? { options } : {}) }));
  const joinRoom = (code, name) => connect(() => send({ type: "join", room_code: code.toUpperCase(), name }));

  useEffect(() => {
    const pending = sessionStorage.getItem("pending_action");
    if (pending && !tokenRef.current) {
      try {
        const { roomCode: rc, playerName, options } = JSON.parse(pending);
        sessionStorage.removeItem("pending_action");
        setPendingIntent(rc ? { kind: "join", code: rc } : { kind: "create" });
        if (rc) joinRoom(rc, playerName);
        else createRoom(playerName, options);
      } catch {
        sessionStorage.removeItem("pending_action");
      }
    } else if (!tokenRef.current) {
      // Rejoin an existing seat (e.g. after a page reload) — the token
      // survives in sessionStorage and connect() auths with tokenRef.
      const saved = sessionStorage.getItem("game_token");
      if (saved) {
        tokenRef.current = saved;
        setPendingIntent({ kind: "reconnect" });
        connect();
      }
    }
  }, []);

  const startGame = () => send({ type: "start" });
  const submitAction = (action) => send({ type: "action", action });

  return {
    connected, roomCode, playerId, isHost, lobby,
    gameStarted, gameState, phaseInfo, yourTurn, waitingFor,
    gameLogs, gameOver, error, pendingIntent,
    cancelPending: () => setPendingIntent(null),
    createRoom, joinRoom, startGame, submitAction,
  };
}

// ============================================================
// SMALL UI PIECES
// ============================================================

const FONT = "'Georgia', serif";

function Btn({ children, onClick, disabled, variant = "primary", small, style: xs }) {
  const V = {
    primary:   { bg: "linear-gradient(135deg,#3f6212,#365314)", color: "#ecfccb", border: "1px solid #365314" },
    secondary: { bg: "#fefce8", color: "#3f6212", border: "1px solid #a3a380" },
    danger:    { bg: "#fee2e2", color: "#dc2626", border: "1px solid #fca5a5" },
  };
  const s = V[variant];
  return (
    <button onClick={onClick} disabled={disabled}
      style={{
        background: disabled ? "#e7e5d8" : s.bg, color: disabled ? "#a8a29e" : s.color,
        border: disabled ? "1px solid #d6d3c1" : s.border, borderRadius: 6,
        padding: small ? "3px 8px" : "6px 14px", fontSize: small ? 11 : 13,
        fontWeight: 700, cursor: disabled ? "not-allowed" : "pointer", fontFamily: "inherit", ...xs,
      }}>{children}</button>
  );
}

function GoodChip({ good, count, small }) {
  if (!count) return null;
  return (
    <span style={{
      display: "inline-flex", alignItems: "center", gap: 2,
      background: `${GOODS[good].color}18`, border: `1px solid ${GOODS[good].color}55`,
      borderRadius: 10, padding: small ? "0 5px" : "1px 7px", fontSize: small ? 11 : 12,
      fontWeight: 700, color: "#292524",
    }}>
      {count}{GOODS[good].icon}
    </span>
  );
}

function Stepper({ value, onChange, min = 0, max = 99 }) {
  return (
    <span style={{ display: "inline-flex", alignItems: "center", gap: 4 }}>
      <Btn small variant="secondary" disabled={value <= min} onClick={() => onChange(value - 1)}>−</Btn>
      <span style={{ minWidth: 18, textAlign: "center", fontWeight: 700 }}>{value}</span>
      <Btn small variant="secondary" disabled={value >= max} onClick={() => onChange(value + 1)}>+</Btn>
    </span>
  );
}

// ============================================================
// FARMYARD BOARD
// ============================================================

const CELL = 62, GAP = 9, PAD = 10;
const FARM_W = PAD * 2 + COLS * CELL + (COLS - 1) * GAP;
const FARM_H = PAD * 2 + ROWS * CELL + (ROWS - 1) * GAP;

function cellXY(idx) {
  const r = Math.floor(idx / COLS), c = idx % COLS;
  return { x: PAD + c * (CELL + GAP), y: PAD + r * (CELL + GAP) };
}

function edgeRect(edge) {
  const [kind, rs, cs] = edge.split("-");
  const r = +rs, c = +cs;
  if (kind === "h") {
    return { x: PAD + c * (CELL + GAP), y: PAD + r * (CELL + GAP) - GAP, w: CELL, h: GAP };
  }
  return { x: PAD + c * (CELL + GAP) - GAP, y: PAD + r * (CELL + GAP), w: GAP, h: CELL };
}

const HOUSE_STYLE = {
  wood:  { bg: "#a16207", label: "🏠" },
  clay:  { bg: "#c2410c", label: "🏠" },
  stone: { bg: "#78716c", label: "🏠" },
};

function FarmYard({ player, mode, selection, onCellClick, onEdgeClick, plannedFences, plannedCells }) {
  // mode: null | "cells" | "edges"; plannedFences: Set of edge keys being added
  const allEdges = useMemo(() => {
    const out = [];
    for (let r = 0; r <= ROWS; r++) for (let c = 0; c < COLS; c++) out.push(`h-${r}-${c}`);
    for (let r = 0; r < ROWS; r++) for (let c = 0; c <= COLS; c++) out.push(`v-${r}-${c}`);
    return out;
  }, []);
  const fenceSet = new Set(player.fences);
  const planned = plannedFences || new Set();

  return (
    <div style={{
      position: "relative", width: FARM_W, height: FARM_H, background: "#d9f99d",
      borderRadius: 8, border: "2px solid #65a30d", boxShadow: "inset 0 0 30px #bef26466",
    }}>
      {player.cells.map((cell, idx) => {
        const { x, y } = cellXY(idx);
        const clickable = mode === "cells" && onCellClick;
        const isPlanned = plannedCells?.has?.(idx);
        let bg = "#bef264", content = null;
        if (cell.type === "room") {
          bg = HOUSE_STYLE[player.house_type].bg;
          content = <span style={{ fontSize: 24 }}>🏠</span>;
        } else if (cell.type === "field") {
          bg = "#a16207";
          content = cell.crops ? (
            <span style={{ fontSize: 13, fontWeight: 800, color: "#fef9c3" }}>
              {GOODS[cell.crops.type].icon}×{cell.crops.count}
            </span>
          ) : <span style={{ fontSize: 18, opacity: 0.6 }}>🟫</span>;
        } else {
          const bits = [];
          if (cell.stable) bits.push(<span key="s" style={{ fontSize: 16 }}>🛖</span>);
          if (cell.animal) bits.push(
            <span key="a" style={{ fontSize: 12, fontWeight: 800 }}>
              {GOODS[cell.animal.type].icon}×{cell.animal.count}
            </span>);
          content = <>{bits}</>;
        }
        return (
          <div key={idx}
            onClick={clickable ? () => onCellClick(idx) : undefined}
            style={{
              position: "absolute", left: x, top: y, width: CELL, height: CELL,
              background: isPlanned ? "#fde047" : bg, borderRadius: 4,
              display: "flex", flexDirection: "column", alignItems: "center", justifyContent: "center",
              cursor: clickable ? "pointer" : "default",
              outline: selection?.has?.(idx) ? "3px solid #f59e0b" : "1px solid #86a83955",
              transition: "background 0.15s",
            }}>
            {content}
          </div>
        );
      })}
      {allEdges.map((edge) => {
        const has = fenceSet.has(edge);
        const isPlanned = planned.has(edge);
        if (!has && !isPlanned && mode !== "edges") return null;
        const { x, y, w, h } = edgeRect(edge);
        const clickable = mode === "edges" && !has && onEdgeClick;
        return (
          <div key={edge}
            onClick={clickable || (isPlanned && onEdgeClick) ? () => onEdgeClick(edge) : undefined}
            style={{
              position: "absolute", left: x, top: y, width: w, height: h, zIndex: 2,
              background: has ? "#7c2d12" : isPlanned ? "#f59e0b" : "#7c2d1222",
              borderRadius: 3, cursor: clickable || isPlanned ? "pointer" : "default",
            }} />
        );
      })}
    </div>
  );
}

// ============================================================
// PLAYER PANEL
// ============================================================

function PlayerPanel({ player, color, isYou, isCurrent, isStarting, state, children }) {
  const totals = animalTotals(player);
  return (
    <div style={{
      background: "#fefce8", border: `2px solid ${isCurrent ? color.bg : "#d6d3c1"}`,
      borderRadius: 10, padding: 10, boxShadow: isCurrent ? `0 0 12px ${color.bg}55` : "none",
    }}>
      <div style={{ display: "flex", alignItems: "center", gap: 8, marginBottom: 6, flexWrap: "wrap" }}>
        <span style={{ width: 14, height: 14, borderRadius: "50%", background: color.bg, display: "inline-block" }} />
        <b style={{ fontSize: 14 }}>{player.name}{isYou ? " (you)" : ""}</b>
        {isStarting && <span title="Starting player" style={{ fontSize: 13 }}>⭐</span>}
        <span style={{ fontSize: 11, color: "#57534e" }}>
          👤{player.people_total - player.people_placed}/{player.people_total}
        </span>
        {Object.entries(player.pets || {}).map(([t, n]) => n > 0 && (
          <span key={t} title="House pets" style={{ fontSize: 11 }}>🏠{GOODS[t].icon}{n > 1 ? `×${n}` : ""}</span>
        ))}
        {player.begging > 0 && <span style={{ fontSize: 11, color: "#dc2626", fontWeight: 700 }}>🥺×{player.begging}</span>}
        <span title="Hand: occupations + minor improvements" style={{ fontSize: 11, color: "#57534e", marginLeft: "auto" }}>
          🂠{(Array.isArray(player.hand_occupations) ? player.hand_occupations.length : player.hand_occupations)
            + (Array.isArray(player.hand_minors) ? player.hand_minors.length : player.hand_minors)}
        </span>
      </div>
      <div style={{ display: "flex", gap: 4, flexWrap: "wrap", marginBottom: 6 }}>
        {Object.keys(GOODS).filter((g) => !ANIMALS.includes(g)).map((g) => (
          <GoodChip key={g} good={g} count={player.resources[g]} small />
        ))}
        {ANIMALS.map((a) => <GoodChip key={a} good={a} count={totals[a]} small />)}
      </div>
      {children}
      {(player.improvements.length > 0 || inPlay(player).length > 0) && (
        <div style={{ display: "flex", gap: 4, flexWrap: "wrap", marginTop: 6 }}>
          {player.improvements.map((imp) => (
            <span key={imp} title={IMPROVEMENTS[imp].desc} style={{
              fontSize: 10, background: "#fecaca55", border: "1px solid #f87171",
              borderRadius: 6, padding: "1px 6px", fontWeight: 700, color: "#7f1d1d",
            }}>{IMPROVEMENTS[imp].name}</span>
          ))}
          {(player.occupations || []).map((inst) => (
            <span key={inst.id} title={cardSpec(inst.id).text} style={{
              fontSize: 10, background: "#fef9c3", border: "1px solid #eab308",
              borderRadius: 6, padding: "1px 6px", fontWeight: 700, color: "#713f12",
            }}>{cardSpec(inst.id).name}</span>
          ))}
          {(player.minors || []).map((inst) => (
            <span key={inst.id} title={cardSpec(inst.id).text + (inst.crops ? ` — planted: ${inst.crops.count} ${inst.crops.type}` : "")} style={{
              fontSize: 10, background: "#ffedd5", border: "1px solid #fb923c",
              borderRadius: 6, padding: "1px 6px", fontWeight: 700, color: "#7c2d12",
            }}>{cardSpec(inst.id).name}{inst.crops ? ` ${GOODS[inst.crops.type].icon}×${inst.crops.count}` : ""}</span>
          ))}
        </div>
      )}
    </div>
  );
}

// ── Hand panel (own cards) ──────────────────────────────────

const HAND_CARD_W = 150;
const ZOOM_CARD_W = 290;

// Hover-zoom preview of a card, pinned next to the hovered hand card
// and clamped to the viewport. Pointer-transparent so it never
// interferes with clicking the card underneath.
function CardZoom({ rect, cid, spec }) {
  const h = Math.round(ZOOM_CARD_W * 1.545);
  const left = rect.right + 8 + ZOOM_CARD_W <= window.innerWidth
    ? rect.right + 8 : Math.max(4, rect.left - 8 - ZOOM_CARD_W);
  const top = Math.min(Math.max(4, rect.top - (h - rect.height) / 2), window.innerHeight - h - 4);
  return (
    <div style={{ position: "fixed", left, top, zIndex: 1000, pointerEvents: "none" }}>
      <AgricolaCard cid={cid} spec={spec} width={ZOOM_CARD_W} />
    </div>
  );
}

function HandCard({ cid, spec: specOverride, playable, selected, onClick, extra }) {
  const [zoomRect, setZoomRect] = useState(null);
  const zoomTimer = useRef(null);
  const spec = specOverride || cardSpec(cid);
  return (
    <div style={{ position: "relative", flexShrink: 0 }}
      onMouseEnter={(e) => {
        const rect = e.currentTarget.getBoundingClientRect();
        zoomTimer.current = setTimeout(() => setZoomRect(rect), 250);
      }}
      onMouseLeave={() => { clearTimeout(zoomTimer.current); setZoomRect(null); }}>
      <AgricolaCard cid={cid} spec={spec} width={HAND_CARD_W}
        playable={playable} selected={selected} onClick={onClick} />
      {extra}
      {zoomRect && <CardZoom rect={zoomRect} cid={cid} spec={spec} />}
    </div>
  );
}

function HandPanel({ me, playableMinors }) {
  const [open, setOpen] = useState(true);
  if (!me || !Array.isArray(me.hand_occupations)) return null;
  const total = me.hand_occupations.length + me.hand_minors.length;
  return (
    <div style={{ marginTop: 10 }}>
      <div style={{ display: "flex", alignItems: "center", gap: 8, marginBottom: 4 }}>
        <div style={{ fontSize: 11, fontWeight: 800, color: "#57534e", textTransform: "uppercase" }}>
          Your hand ({total})
        </div>
        <Btn small variant="secondary" onClick={() => setOpen(!open)}>{open ? "Hide" : "Show"}</Btn>
      </div>
      {open && (
        <div style={{ display: "flex", gap: 6, flexWrap: "wrap" }}>
          {me.hand_occupations.map((cid) => <HandCard key={cid} cid={cid} />)}
          {me.hand_minors.map((cid) => (
            <HandCard key={cid} cid={cid} playable={playableMinors?.includes(cid)} />
          ))}
        </div>
      )}
    </div>
  );
}

// ============================================================
// ACTION BOARD
// ============================================================

// Engine-canonical board geometry (server/agricola/state.py
// SPACE_POSITIONS / ROUND_SLOTS): every space is a rect
// [col, top, height] with top/height in HALF-ROWS of the base grid (a
// 1-row box is 2 half-rows; round spaces are 4; the 3p extension
// strip's boxes 3). col -1 is the extension strip, 0 the scroll
// column, 1 the accumulation column, 2-8 the round columns. Rounds
// 1-7 run along the TOP of the board, 8-13 in a second band, and 14
// sits alone at the bottom of column 2 -- the printed board is cut
// away below the shorter bands (the photo's stepped cliff edge),
// rendered here as exposed table. The card revealed in round N always
// sits at ROUND_SLOTS[N], so adjacency cards read the same on screen
// as in the engine.
const BASE_POS = {
  farm_expansion: [0, 0, 2], meeting_place: [0, 2, 2], grain_seeds: [0, 4, 2],
  farmland: [0, 6, 2], lessons: [0, 8, 2], day_laborer: [0, 10, 2],
  forest: [1, 4, 2], clay_pit: [1, 6, 2], reed_bank: [1, 8, 2], fishing: [1, 10, 2],
};
const EXT_POS_3P = {
  grove: [-1, 0, 3], resource_market_3p: [-1, 3, 3],
  hollow_3p: [-1, 6, 3], lessons_b: [-1, 9, 3],
};
const EXT_POS_4P = {
  copse: [-1, 0, 2], grove: [-1, 2, 2], resource_market_4p: [-1, 4, 2],
  hollow_4p: [-1, 6, 2], lessons_b: [-1, 8, 2], traveling_players: [-1, 10, 2],
};
const boardPosFor = (playerCount) =>
  playerCount >= 4 ? { ...BASE_POS, ...EXT_POS_4P }
  : playerCount === 3 ? { ...BASE_POS, ...EXT_POS_3P }
  : BASE_POS;
const ROUND_SLOTS = {};
for (let n = 1; n <= 7; n++) ROUND_SLOTS[n] = [1 + n, 0, 4];
for (let n = 8; n <= 13; n++) ROUND_SLOTS[n] = [n - 6, 4, 4];
ROUND_SLOTS[14] = [2, 8, 4];
const stageOfRound = (r) => r <= 4 ? 1 : r <= 7 ? 2 : r <= 9 ? 3 : r <= 11 ? 4 : r <= 13 ? 5 : 6;
// Official majors-board arrangement: hearths + Well on top, ovens and
// crafts below.
const MAJORS_LAYOUT = [
  "fireplace_2", "fireplace_3", "cooking_hearth_4", "cooking_hearth_5", "well",
  "clay_oven", "stone_oven", "joinery", "pottery", "basketmaker",
];

const BOARD_FONT = "'Cinzel', Georgia, serif";
const GRASS_NOISE = `url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='120' height='120'%3E%3Cfilter id='n'%3E%3CfeTurbulence type='fractalNoise' baseFrequency='0.9' numOctaves='2'/%3E%3CfeColorMatrix values='0 0 0 0 0  0 0 0 0 0  0 0 0 0 0  0 0 0 0.05 0'/%3E%3C/filter%3E%3Crect width='120' height='120' filter='url(%23n)'/%3E%3C/svg%3E")`;

function WorkerDiscs({ space, players }) {
  const idxs = [space.occupied_by, ...(space.extra_occupants || [])]
    .filter((i) => i !== null && i !== undefined);
  if (!idxs.length) return null;
  return (
    <span style={{ display: "inline-flex", gap: 2, flexShrink: 0 }}>
      {idxs.map((i, k) => (
        <span key={k} title={players[i]?.name} style={{
          width: 13, height: 13, borderRadius: "50%",
          background: PLAYER_COLORS[i].bg,
          border: "1.5px solid #fff",
          boxShadow: "0 1px 2px rgba(0,0,0,0.5)",
        }} />
      ))}
    </span>
  );
}

// One physical action space on the board (scroll, accumulation, or a
// revealed round card).
function BoardSpace({ sp, valid, onPick, players, round, gridPos }) {
  const occupied = sp.occupied_by !== null && sp.occupied_by !== undefined;
  const isRoundCard = round !== undefined;
  const base = sp.accumulates
    ? "linear-gradient(170deg,#efdfb4 0%,#e2cd94 55%,#d5bc7e 100%)"
    : isRoundCard
      ? "linear-gradient(175deg,#fdf6e0 0%,#f4e7c1 100%)"
      : "linear-gradient(170deg,#f8f0d8 0%,#efe0b8 60%,#e6d3a3 100%)";
  return (
    <div
      className={valid ? "agri-valid" : undefined}
      onClick={valid ? () => onPick(sp.id) : undefined}
      title={`${sp.name} — ${sp.desc}`}
      style={{
        gridColumn: gridPos[0], gridRow: gridPos[1],
        background: base,
        border: valid ? "2px solid #f59e0b" : "1px solid #a8895a",
        boxShadow: valid
          ? "0 0 0 3px rgba(245,158,11,0.35), 0 3px 5px rgba(30,50,15,0.4)"
          : "inset 0 1px 0 rgba(255,255,255,0.65), inset 0 -6px 10px rgba(160,130,70,0.22), 0 2px 4px rgba(30,50,15,0.35)",
        borderRadius: 7, padding: "4px 5px",
        display: "flex", flexDirection: "column", overflow: "hidden",
        opacity: occupied ? 0.82 : 1, position: "relative",
        filter: occupied ? "saturate(0.65)" : "none",
      }}>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "flex-start", gap: 2 }}>
        <b style={{
          fontFamily: BOARD_FONT, fontSize: 9.5, lineHeight: 1.15,
          color: "#43331a", letterSpacing: 0.2, flex: 1, minWidth: 0,
          hyphens: "auto", WebkitHyphens: "auto",
        }}>{sp.name}</b>
        {isRoundCard ? (
          <span title={`Revealed in round ${round}${HARVEST_ROUNDS.includes(round) ? " — harvest" : ""}`}
            style={{
              flexShrink: 0, minWidth: 15, height: 15, borderRadius: 8, padding: "0 3px",
              background: "linear-gradient(160deg,#5f8f3e,#456f2c)", color: "#f2f8e4",
              fontSize: 8.5, fontWeight: 800, display: "inline-flex",
              alignItems: "center", justifyContent: "center", gap: 1,
              border: "1px solid #37591f",
            }}>{round}{HARVEST_ROUNDS.includes(round) ? "🌾" : ""}</span>
        ) : null}
      </div>
      <div style={{ fontSize: 7.5, color: "#6d5a3a", lineHeight: 1.25, marginTop: 1, flex: 1, overflow: "hidden" }}>
        {sp.desc}
      </div>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "flex-end", gap: 2 }}>
        <div style={{ display: "flex", gap: 2, flexWrap: "wrap" }}>
          {Object.entries(sp.supply || {}).map(([good, count]) => (
            <GoodChip key={good} good={good} count={count} small />
          ))}
        </div>
        <WorkerDiscs space={sp} players={players} />
      </div>
    </div>
  );
}

// A face-down (not yet revealed) round slot, printed on the board.
function RoundSlot({ round, gridPos }) {
  return (
    <div title={`Round ${round} — revealed at the start of that round`}
      style={{
        gridColumn: gridPos[0], gridRow: gridPos[1],
        background: "rgba(15,35,8,0.22)",
        border: "1.5px dashed rgba(245,240,210,0.4)", borderRadius: 8,
        display: "flex", flexDirection: "column", alignItems: "center", justifyContent: "center",
        color: "rgba(250,245,220,0.9)", gap: 1, textShadow: "0 1px 2px rgba(0,0,0,0.5)",
      }}>
      <div style={{ fontFamily: BOARD_FONT, fontSize: 12, fontWeight: 700 }}>Round {round}</div>
      <div style={{ fontSize: 8.5, opacity: 0.85 }}>Stage {stageOfRound(round)}</div>
      {HARVEST_ROUNDS.includes(round) && <div style={{ fontSize: 10 }} title="Harvest after this round">🌾</div>}
    </div>
  );
}

// The red supply board holding the major improvements -- physically a
// separate board, rendered resting on the table in the main board's
// cut-away corner.
function MajorsBoard({ available }) {
  const openSet = new Set(available);
  return (
    <div style={{
      flex: 1, minWidth: 0,
      background: "linear-gradient(165deg,#8e3b2c 0%,#7a2f22 60%,#6c2a1e 100%)",
      border: "1px solid #4e1d13", borderRadius: 8, padding: "5px 7px 7px",
      boxShadow: "inset 0 1px 0 rgba(255,220,190,0.25), 0 3px 6px rgba(30,20,10,0.45)",
      display: "flex", flexDirection: "column",
    }}>
      <div style={{
        fontFamily: BOARD_FONT, fontSize: 10, fontWeight: 700, letterSpacing: 1.5,
        color: "#f3d9a8", textTransform: "uppercase", textAlign: "center", marginBottom: 4,
        textShadow: "0 1px 2px rgba(0,0,0,0.6)",
      }}>Major Improvements</div>
      <div style={{ display: "grid", gridTemplateColumns: "repeat(5, 1fr)", gap: 4, flex: 1 }}>
        {MAJORS_LAYOUT.map((imp) => {
          const spec = IMPROVEMENTS[imp];
          const open = openSet.has(imp);
          return open ? (
            <div key={imp}
              title={`${spec.name} — ${spec.desc} · cost: ${Object.entries(spec.cost).map(([g, n]) => `${n} ${g}`).join(", ")}`}
              style={{
                background: "linear-gradient(175deg,#f9efd6,#eeddb2)",
                border: "1px solid #9c7c4c", borderRadius: 5, padding: "3px 4px",
                boxShadow: "0 2px 3px rgba(30,20,10,0.5)",
                display: "flex", flexDirection: "column", gap: 1, overflow: "hidden",
              }}>
              <div style={{ display: "flex", justifyContent: "space-between", gap: 2 }}>
                <b style={{ fontFamily: BOARD_FONT, fontSize: 8.5, lineHeight: 1.15, color: "#43331a" }}>
                  {spec.name}
                </b>
                <span style={{ fontSize: 8, fontWeight: 800, color: "#7f1d1d", flexShrink: 0 }}>⭐{spec.points}</span>
              </div>
              <div style={{ display: "flex", gap: 2, flexWrap: "wrap", marginTop: "auto" }}>
                {Object.entries(spec.cost).map(([g, n]) => (
                  <span key={g} style={{ fontSize: 8, fontWeight: 700, color: "#57431f" }}>{n}{GOODS[g].icon}</span>
                ))}
              </div>
            </div>
          ) : (
            <div key={imp} title={`${spec.name} — built`} style={{
              border: "1px dashed rgba(240,210,170,0.35)", borderRadius: 5,
              background: "rgba(40,15,8,0.25)",
              display: "flex", alignItems: "center", justifyContent: "center",
              fontSize: 7.5, color: "rgba(240,215,180,0.55)", textAlign: "center", padding: 2,
              fontStyle: "italic",
            }}>{spec.name}<br />built</div>
          );
        })}
      </div>
    </div>
  );
}

function ActionBoard({ state, validSpaces, onPick, players }) {
  const byId = {};
  state.action_spaces.forEach((sp) => { byId[sp.id] = sp; });
  // Round number of each revealed stage card (reveal order = round order).
  const roundOf = {};
  (state.revealed || []).forEach((cid, i) => { roundOf[cid] = i + 1; });

  const POS = boardPosFor(players.length);
  const ext = state.action_spaces.some((sp) => (POS[sp.id] || [0])[0] === -1);
  const off = ext ? 2 : 1; // grid columns are 1-based; shift right if strip present
  const cols = 9 + (ext ? 1 : 0);
  // Rects are [col, top, height] in half-rows; the CSS grid uses 12
  // half-rows of 40px (2 half-rows + gap = the old 86px full row).
  const cell = ([x, top, h]) => [`${x + off}`, `${top + 1} / span ${h}`];

  // Spaces the printed grid doesn't know: card-created action spaces
  // (Chapel, Forest Inn, ...) and anything else unpositioned.
  const looseSpaces = state.action_spaces.filter(
    (sp) => !(sp.id in POS) && !(sp.id in roundOf));

  const roundsLeft = 14 - (state.revealed || []).length;
  // The printed board is cut away below the shorter round bands; the
  // exposed table hosts the (physically separate) majors supply board
  // and the harvest reference, plus the face-down stage-card pile.
  const tableStyle = {
    background: "linear-gradient(160deg,#63482c 0%,#523a22 55%,#46311c 100%)",
    borderRadius: 6, margin: -4, zIndex: 0,
    boxShadow: "inset 0 3px 10px rgba(15,10,4,0.55), inset 0 -1px 0 rgba(255,225,180,0.12)",
  };

  return (
    <div style={{ width: "fit-content" }}>
      <style>{`
        .agri-valid { cursor: pointer; transition: transform .12s ease, box-shadow .12s ease; }
        .agri-valid:hover { transform: translateY(-2px);
          box-shadow: 0 0 0 3px rgba(245,158,11,0.55), 0 6px 10px rgba(30,50,15,0.5) !important; }
      `}</style>
      {/* Wooden frame */}
      <div style={{
        background: "linear-gradient(160deg,#7c5a37 0%,#654728 55%,#54391f 100%)",
        borderRadius: 14, padding: 9,
        boxShadow: "0 6px 16px rgba(30,25,10,0.35), inset 0 1px 0 rgba(255,230,190,0.35)",
      }}>
        {/* Meadow */}
        <div style={{
          position: "relative", borderRadius: 8, padding: 8,
          background: `
            radial-gradient(230px 170px at 22% 28%, rgba(255,255,215,0.14), transparent 70%),
            radial-gradient(280px 210px at 72% 62%, rgba(45,75,20,0.16), transparent 70%),
            radial-gradient(190px 150px at 46% 88%, rgba(255,255,215,0.10), transparent 70%),
            linear-gradient(155deg,#93b164 0%,#7fa254 45%,#6f9449 100%)`,
          boxShadow: "inset 0 0 18px rgba(25,45,10,0.32)",
        }}>
          <div style={{
            position: "absolute", inset: 0, borderRadius: 8,
            background: GRASS_NOISE, pointerEvents: "none",
          }} />
          <div style={{
            position: "relative", display: "grid", gap: 6,
            gridTemplateColumns: `repeat(${cols}, 92px)`,
            gridTemplateRows: "repeat(12, 40px)",
          }}>
            {/* Fixed action spaces at their printed positions */}
            {state.action_spaces.map((sp) => {
              const pos = POS[sp.id];
              if (!pos) return null;
              return <BoardSpace key={sp.id} sp={sp} players={players}
                valid={validSpaces.has(sp.id)} onPick={onPick}
                gridPos={cell(pos)} />;
            })}
            {/* Round track: revealed cards, then face-down slots */}
            {Object.entries(ROUND_SLOTS).map(([r, pos]) => {
              const round = Number(r);
              const cid = (state.revealed || [])[round - 1];
              const sp = cid && byId[cid];
              return sp
                ? <BoardSpace key={`r${round}`} sp={sp} players={players}
                    valid={validSpaces.has(sp.id)} onPick={onPick}
                    round={round} gridPos={cell(pos)} />
                : <RoundSlot key={`r${round}`} round={round} gridPos={cell(pos)} />;
            })}
            {/* Stepped cut-away right of round 7: the face-down pile
                of coming stage cards rests on the table there */}
            <div style={{
              ...tableStyle, gridColumn: `${8 + off}`, gridRow: "5 / span 4",
              display: "flex", alignItems: "center", justifyContent: "center", padding: 6,
            }}>
              {roundsLeft > 0 && (
                <div title={`${roundsLeft} action space card${roundsLeft === 1 ? "" : "s"} still face down`}
                  style={{
                    width: 62, height: 88, borderRadius: 6,
                    background: "linear-gradient(160deg,#3e5e28,#2c4519 70%)",
                    border: "1px solid #1e3010",
                    boxShadow: "0 2px 0 #24361a, 0 4px 0 #1d2c14, 0 6px 8px rgba(0,0,0,0.4)",
                    display: "flex", flexDirection: "column", alignItems: "center",
                    justifyContent: "center", gap: 2,
                    color: "rgba(240,245,215,0.85)",
                  }}>
                  <div style={{ fontFamily: BOARD_FONT, fontSize: 16, fontWeight: 700 }}>{roundsLeft}</div>
                  <div style={{ fontSize: 7.5, textAlign: "center", lineHeight: 1.2 }}>cards<br />to come</div>
                </div>
              )}
            </div>
            {/* Stepped cut-away below round bands 8-13: the majors
                supply board and harvest reference sit on the table */}
            <div style={{
              ...tableStyle, gridColumn: `${3 + off} / span 6`, gridRow: "9 / span 4",
              display: "flex", gap: 8, padding: 8, alignItems: "stretch",
            }}>
              <MajorsBoard available={state.available_improvements || []} />
              <div style={{
                width: 148, flexShrink: 0,
                background: "linear-gradient(170deg,#f4ebcf 0%,#e9d8ab 100%)",
                border: "1px solid #a8895a", borderRadius: 7, padding: "7px 9px",
                boxShadow: "inset 0 1px 0 rgba(255,255,255,0.65), 0 2px 4px rgba(30,50,15,0.35)",
                display: "flex", flexDirection: "column", gap: 3,
                fontSize: 8, color: "#5b4a2c", lineHeight: 1.3,
              }}>
                <div style={{ fontFamily: BOARD_FONT, fontSize: 10, fontWeight: 700, color: "#43331a", textAlign: "center" }}>
                  🌾 Harvest
                </div>
                <div style={{ textAlign: "center", marginTop: -2 }}>
                  after rounds 4 · 7 · 9 · 11 · 13 · 14
                </div>
                <div><b>1. Field</b> — reap 1 grain/vegetable from each sown field</div>
                <div><b>2. Feed</b> — 2 food per person (1 for newborns)</div>
                <div><b>3. Breed</b> — 2+ animals of a kind bear one more</div>
                <div style={{ borderTop: "1px solid #c4ab77", marginTop: "auto", paddingTop: 3, textAlign: "center", fontStyle: "italic" }}>
                  The game ends with the round-14 harvest.
                </div>
              </div>
            </div>
          </div>
        </div>
      </div>
      {/* Card-created action spaces (Chapel, Forest Inn, ...) */}
      {looseSpaces.length > 0 && (
        <div style={{ display: "flex", gap: 6, flexWrap: "wrap", marginTop: 8, alignItems: "stretch" }}>
          {looseSpaces.map((sp) => {
            const owner = sp.owner !== undefined ? players[sp.owner] : null;
            return (
              <div key={sp.id} style={{ width: 148, display: "flex", flexDirection: "column" }}>
                {owner && (
                  <div style={{ fontSize: 9, color: "#57534e", marginBottom: 1 }}>
                    <span style={{
                      display: "inline-block", width: 8, height: 8, borderRadius: "50%",
                      background: PLAYER_COLORS[owner.index].bg, marginRight: 3,
                    }} />{owner.name}
                  </div>
                )}
                <BoardSpace sp={sp} players={players}
                  valid={validSpaces.has(sp.id)} onPick={onPick}
                  gridPos={["auto", "auto"]} />
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}

// ============================================================
// PLANNERS (parameterized actions)
// ============================================================

function PlannerShell({ title, children, onCancel, onSubmit, submitLabel = "Confirm", submitDisabled }) {
  return (
    <div style={{
      background: "#fffbeb", border: "2px solid #d97706", borderRadius: 10,
      padding: 12, marginBottom: 10,
    }}>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 8 }}>
        <b style={{ fontSize: 14 }}>{title}</b>
        <div style={{ display: "flex", gap: 6 }}>
          <Btn small variant="secondary" onClick={onCancel}>Cancel</Btn>
          <Btn small onClick={onSubmit} disabled={submitDisabled}>{submitLabel}</Btn>
        </div>
      </div>
      {children}
    </div>
  );
}

function BakePlanner({ me, bake, setBake, grainBudget }) {
  const bakers = me.improvements.filter((i) => BAKE_VALUES[i]);
  if (!bakers.length) return <div style={{ fontSize: 12 }}>No baking improvements.</div>;
  const used = Object.values(bake).reduce((a, b) => a + b, 0);
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 4 }}>
      <div style={{ fontSize: 12, fontWeight: 700 }}>
        Bake bread ({grainBudget - used} grain left):
      </div>
      {bakers.map((imp) => {
        const [limit, value] = BAKE_VALUES[imp];
        const cur = bake[imp] || 0;
        const max = Math.min(limit ?? 99, cur + grainBudget - used);
        return (
          <div key={imp} style={{ display: "flex", alignItems: "center", gap: 8, fontSize: 12 }}>
            <span style={{ minWidth: 150 }}>{IMPROVEMENTS[imp].name} ({value} food/grain{limit ? `, max ${limit}` : ""})</span>
            <Stepper value={cur} min={0} max={max}
              onChange={(v) => setBake({ ...bake, [imp]: v })} />
          </div>
        );
      })}
    </div>
  );
}

function SowPlanner({ me, sow, setSow }) {
  // sow: {targetKey: "grain"|"vegetable"}; targetKey = cell index (number
  // as string) or "card:<id>" for card fields (Beanfield etc.)
  const targets = me.cells.map((c, i) => ({ key: String(i), label: `Field ${i}`, allowed: ["grain", "vegetable"], empty: c.type === "field" && !c.crops }))
    .filter((t) => t.empty);
  for (const inst of (me.minors || [])) {
    const spec = cardSpec(inst.id);
    if (spec.field && !inst.crops) {
      targets.push({ key: `card:${inst.id}`, label: spec.name, allowed: spec.field.crops, empty: true });
    }
  }
  const used = { grain: 0, vegetable: 0 };
  Object.values(sow).forEach((crop) => used[crop]++);
  const cycle = (t) => {
    const cur = sow[t.key];
    const next = { ...sow };
    const options = t.allowed.filter((crop) => me.resources[crop] - used[crop] > 0 || sow[t.key] === crop);
    const seq = [undefined, ...options];
    const pos = seq.indexOf(cur);
    const nxt = seq[(pos + 1) % seq.length];
    if (nxt === undefined) delete next[t.key];
    else next[t.key] = nxt;
    setSow(next);
  };
  if (!targets.length) return <div style={{ fontSize: 12 }}>No empty fields to sow.</div>;
  return (
    <div style={{ fontSize: 12 }}>
      <div style={{ fontWeight: 700, marginBottom: 4 }}>
        Sow (click a field to cycle crops):
      </div>
      <div style={{ display: "flex", gap: 6, flexWrap: "wrap" }}>
        {targets.map((t) => (
          <Btn key={t.key} small variant="secondary" onClick={() => cycle(t)}>
            {t.label}: {sow[t.key] ? GOODS[sow[t.key]].icon : "—"}
          </Btn>
        ))}
      </div>
    </div>
  );
}

function sowListFrom(sow) {
  return Object.entries(sow).map(([key, crop]) =>
    key.startsWith("card:") ? { card: key.slice(5), crop } : { cell: +key, crop });
}

// Picker for playing a minor improvement from hand.
function MinorPicker({ me, playableMinors, chosen, setChosen, params, setParams, optional }) {
  if (!Array.isArray(me.hand_minors) || !me.hand_minors.length) {
    return <div style={{ fontSize: 12 }}>No minor improvements in hand.</div>;
  }
  const needsCell = chosen === "minor_shifting_cultivation";
  return (
    <div>
      <div style={{ fontSize: 12, fontWeight: 700, marginBottom: 4 }}>
        {optional ? "Optionally play a minor improvement:" : "Play a minor improvement:"}
      </div>
      <div style={{ display: "flex", gap: 6, flexWrap: "wrap" }}>
        {me.hand_minors.map((cid) => {
          const playable = playableMinors?.includes(cid);
          return (
            <HandCard key={cid} cid={cid} playable={playable}
              selected={chosen === cid}
              onClick={playable ? () => setChosen(chosen === cid ? null : cid) : undefined} />
          );
        })}
      </div>
      {needsCell && (
        <div style={{ marginTop: 6 }}>
          <div style={{ fontSize: 12, marginBottom: 4 }}>Choose a space to plow:</div>
          <FarmYard player={me} mode="cells"
            plannedCells={new Set(params?.cell !== undefined ? [params.cell] : [])}
            onCellClick={(i) => setParams({ cell: i })} />
        </div>
      )}
    </div>
  );
}

function minorAction(chosen, params) {
  if (!chosen) return undefined;
  const m = { card: chosen };
  if (chosen === "minor_shifting_cultivation" && params?.cell !== undefined)
    m.params = { cell: params.cell };
  return m;
}

// Major improvements come from the local IMPROVEMENTS map (not the
// catalog), so build a renderable spec for the shared card face.
function majorSpec(imp) {
  const m = IMPROVEMENTS[imp];
  return { name: m.name, type: "major", cost: m.cost, points: m.points, text: m.desc };
}

function ImprovementPicker({ state, me, chosen, setChosen, upgrade, setUpgrade }) {
  const ownsFireplace = me.improvements.some((i) => FIREPLACES.includes(i));
  const canAfford = (cost) => Object.entries(cost).every(([r, a]) => me.resources[r] >= a);
  return (
    <div style={{ display: "flex", flexWrap: "wrap", gap: 6, alignItems: "flex-start" }}>
      {state.available_improvements.map((imp) => {
        const spec = IMPROVEMENTS[imp];
        const affordable = canAfford(spec.cost);
        const upgradeable = spec.upgrade && ownsFireplace;
        const selectable = affordable || upgradeable;
        const selected = chosen === imp;
        return (
          <div key={imp} style={{ flexShrink: 0 }}>
            <HandCard cid={imp} spec={majorSpec(imp)} playable={selectable} selected={selected}
              onClick={selectable ? () => { setChosen(selected ? null : imp); setUpgrade(!affordable && upgradeable); } : undefined} />
            {selected && spec.upgrade && ownsFireplace && (
              <label style={{ fontSize: 11, display: "block", marginTop: 4 }}>
                <input type="checkbox" checked={upgrade}
                  onChange={(e) => setUpgrade(e.target.checked)} /> upgrade Fireplace (free)
              </label>
            )}
          </div>
        );
      })}
    </div>
  );
}

function Planner({ space, state, me, actionInfo, submit, cancel, error }) {
  const [cells, setCells] = useState([]);         // rooms / stables / plow
  const [mode, setMode] = useState("rooms");      // farm_expansion toggle
  const [fences, setFences] = useState(new Set());
  const [sow, setSow] = useState({});
  const [bake, setBake] = useState({});
  const [chosenImp, setChosenImp] = useState(null);
  const [upgrade, setUpgrade] = useState(false);
  const [choice, setChoice] = useState("reed");
  const [chosenCard, setChosenCard] = useState(null);   // occupation or minor
  const [cardParams, setCardParams] = useState(null);
  const [tab, setTab] = useState("major");
  const [useLasso, setUseLasso] = useState(false);

  const playableMinors = state.playable_minors || [];
  const grainBudget = me.resources.grain - Object.values(sow).filter((c) => c === "grain").length;
  const sowList = sowListFrom(sow);
  const bakeDict = Object.fromEntries(Object.entries(bake).filter(([, v]) => v > 0));

  const toggleEdge = (edge) => {
    const next = new Set(fences);
    if (next.has(edge)) next.delete(edge); else next.add(edge);
    setFences(next);
  };

  const farmProps = { player: me, plannedFences: fences };
  const canBakeOnSpace = (sid) => inPlay(me).some((inst) =>
    (cardSpec(inst.id).bake_on_spaces || []).includes(sid));

  let title = "", body = null, action = null, disabled = false;

  if (space === "farmland" || space === "cultivation") {
    title = space === "farmland" ? "Plow 1 field" : "Cultivation: plow and/or sow";
    const plowCell = cells[0];
    const extraBake = canBakeOnSpace(space);
    body = (
      <>
        <div style={{ fontSize: 12, marginBottom: 6 }}>Click an empty space to plow{space === "cultivation" ? " (optional)" : ""}.</div>
        <FarmYard {...farmProps} mode="cells" plannedCells={new Set(cells)}
          onCellClick={(i) => setCells(cells[0] === i ? [] : [i])} />
        {space === "cultivation" && <div style={{ marginTop: 8 }}><SowPlanner me={me} sow={sow} setSow={setSow} /></div>}
        {extraBake && (
          <div style={{ marginTop: 8 }}>
            <div style={{ fontSize: 11, color: "#78716c" }}>Threshing Board: extra Bake Bread action</div>
            <BakePlanner me={me} bake={bake} setBake={setBake} grainBudget={grainBudget} />
          </div>
        )}
      </>
    );
    if (space === "farmland") {
      action = { kind: "place", space, cell: plowCell };
      if (Object.keys(bakeDict).length) action.bake = bakeDict;
      disabled = plowCell === undefined;
    } else {
      action = { kind: "place", space, plow: plowCell ?? null, sow: sowList };
      if (plowCell === undefined) delete action.plow;
      if (Object.keys(bakeDict).length) action.bake = bakeDict;
      disabled = plowCell === undefined && !sowList.length;
    }
  } else if (space === "lessons" || space === "lessons_b") {
    const cost = actionInfo?.occ_cost ?? state.occ_costs?.[space] ?? 1;
    title = `Lessons: play an occupation (${cost} food)`;
    body = (
      <>
        <div style={{ display: "flex", gap: 6, flexWrap: "wrap" }}>
          {(me.hand_occupations || []).map((cid) => (
            <HandCard key={cid} cid={cid} selected={chosenCard === cid}
              playable={me.resources.food >= cost}
              onClick={me.resources.food >= cost
                ? () => setChosenCard(chosenCard === cid ? null : cid) : undefined} />
          ))}
        </div>
      </>
    );
    action = { kind: "place", space, card: chosenCard };
    disabled = !chosenCard;
  } else if (space === "meeting_place") {
    title = "Meeting Place: become starting player";
    body = (
      <MinorPicker me={me} playableMinors={playableMinors} optional
        chosen={chosenCard} setChosen={setChosenCard}
        params={cardParams} setParams={setCardParams} />
    );
    action = { kind: "place", space };
    const m = minorAction(chosenCard, cardParams);
    if (m) action.minor = m;
    disabled = chosenCard === "minor_shifting_cultivation" && cardParams?.cell === undefined;
  } else if (space === "basic_wish") {
    title = "Basic Wish for Children: family growth";
    body = (
      <>
        <div style={{ fontSize: 12, marginBottom: 6 }}>
          Your family grows by one person (needs more room than people).
        </div>
        <MinorPicker me={me} playableMinors={playableMinors} optional
          chosen={chosenCard} setChosen={setChosenCard}
          params={cardParams} setParams={setCardParams} />
      </>
    );
    action = { kind: "place", space };
    const m = minorAction(chosenCard, cardParams);
    if (m) action.minor = m;
    disabled = chosenCard === "minor_shifting_cultivation" && cardParams?.cell === undefined;
  } else if (space === "farm_expansion") {
    title = "Farm Expansion: build rooms and/or stables";
    body = (
      <>
        <div style={{ display: "flex", gap: 6, marginBottom: 6 }}>
          <Btn small variant={mode === "rooms" ? "primary" : "secondary"} onClick={() => setMode("rooms")}>
            Rooms (5{GOODS[me.house_type === "wood" ? "wood" : me.house_type].icon} + 2🌿 each)
          </Btn>
          <Btn small variant={mode === "stables" ? "primary" : "secondary"} onClick={() => setMode("stables")}>
            Stables (2🪵 each)
          </Btn>
        </div>
        <div style={{ fontSize: 12, marginBottom: 6 }}>
          Click cells — rooms: {cells.filter((c) => c.t === "room").map((c) => c.i).join(", ") || "none"};
          stables: {cells.filter((c) => c.t === "stable").map((c) => c.i).join(", ") || "none"}
        </div>
        <FarmYard {...farmProps} mode="cells"
          plannedCells={new Set(cells.map((c) => c.i))}
          onCellClick={(i) => {
            const existing = cells.find((c) => c.i === i);
            if (existing) setCells(cells.filter((c) => c.i !== i));
            else setCells([...cells, { i, t: mode === "rooms" ? "room" : "stable" }]);
          }} />
      </>
    );
    const rooms = cells.filter((c) => c.t === "room").map((c) => c.i);
    const stables = cells.filter((c) => c.t === "stable").map((c) => c.i);
    action = { kind: "place", space, rooms, stables };
    disabled = !rooms.length && !stables.length;
  } else if (space === "fencing" || space === "farm_redevelopment") {
    const isReno = space === "farm_redevelopment";
    title = isReno ? "Farm Redevelopment: renovate, then fences" : "Build fences (1 wood each)";
    const hasMiningHammer = inPlay(me).some((i) => i.id === "minor_mining_hammer");
    body = (
      <>
        {isReno && (
          <div style={{ fontSize: 12, marginBottom: 6 }}>
            Renovates your house to {me.house_type === "wood" ? "clay" : "stone"} first
            (1🌿 + 1 per room). Then optionally build fences:
          </div>
        )}
        <div style={{ fontSize: 12, marginBottom: 6 }}>
          Click edges to plan fences — {fences.size} planned ({fences.size}🪵).
          Fences must fully enclose pastures.
        </div>
        <FarmYard {...farmProps} mode="edges" onEdgeClick={toggleEdge} />
        {isReno && hasMiningHammer && (
          <div style={{ fontSize: 12, marginTop: 6 }}>
            Mining Hammer free stable cell:{" "}
            <Btn small variant="secondary" onClick={() => setCells(cells.length ? [] : [0])}>
              {cells.length ? `cell ${cells[0]}` : "none"}
            </Btn>
            {cells.length > 0 && (
              <FarmYard player={me} mode="cells" plannedCells={new Set(cells)}
                onCellClick={(i) => setCells([i])} />
            )}
          </div>
        )}
      </>
    );
    action = { kind: "place", space };
    if (fences.size) action.fences = [...fences];
    if (isReno && cells.length) action.stable = cells[0];
    disabled = isReno ? false : !fences.size;
  } else if (space === "grain_utilization") {
    title = "Grain Utilization: sow and/or bake";
    body = (
      <>
        <SowPlanner me={me} sow={sow} setSow={setSow} />
        <div style={{ marginTop: 8 }}>
          <BakePlanner me={me} bake={bake} setBake={setBake} grainBudget={grainBudget} />
        </div>
      </>
    );
    action = { kind: "place", space, sow: sowList, bake: bakeDict };
    disabled = !sowList.length && !Object.keys(bakeDict).length;
  } else if (space === "major_improvement" || space === "house_redevelopment") {
    const isReno = space === "house_redevelopment";
    title = isReno ? "House Redevelopment: renovate, then improvement"
      : "Major or Minor Improvement";
    const spec = chosenImp ? IMPROVEMENTS[chosenImp] : null;
    const hasMiningHammer = inPlay(me).some((i) => i.id === "minor_mining_hammer");
    body = (
      <>
        {isReno && (
          <div style={{ fontSize: 12, marginBottom: 6 }}>
            Renovates your house to {me.house_type === "wood" ? "clay" : "stone"}
            (1🌿 + 1 per room). Optionally also:
          </div>
        )}
        <div style={{ display: "flex", gap: 6, marginBottom: 6 }}>
          <Btn small variant={tab === "major" ? "primary" : "secondary"}
            onClick={() => { setTab("major"); setChosenCard(null); }}>Major improvement</Btn>
          <Btn small variant={tab === "minor" ? "primary" : "secondary"}
            onClick={() => { setTab("minor"); setChosenImp(null); }}>Minor improvement</Btn>
        </div>
        {tab === "major" ? (
          <>
            <ImprovementPicker state={state} me={me} chosen={chosenImp} setChosen={setChosenImp}
              upgrade={upgrade} setUpgrade={setUpgrade} />
            {spec?.oven && me.resources.grain > 0 && (
              <div style={{ marginTop: 8 }}>
                <div style={{ fontSize: 12, fontWeight: 700 }}>Bake immediately (optional):</div>
                <Stepper value={bake[chosenImp] || 0} min={0}
                  max={Math.min(spec.bakeLimit, me.resources.grain)}
                  onChange={(v) => setBake({ [chosenImp]: v })} />
              </div>
            )}
          </>
        ) : (
          <MinorPicker me={me} playableMinors={playableMinors} optional={isReno}
            chosen={chosenCard} setChosen={setChosenCard}
            params={cardParams} setParams={setCardParams} />
        )}
        {isReno && hasMiningHammer && (
          <div style={{ fontSize: 12, marginTop: 6 }}>
            Mining Hammer free stable cell:{" "}
            <Btn small variant="secondary" onClick={() => setCells(cells.length ? [] : [0])}>
              {cells.length ? `cell ${cells[0]}` : "none"}
            </Btn>
            {cells.length > 0 && (
              <FarmYard player={me} mode="cells" plannedCells={new Set(cells)}
                onCellClick={(i) => setCells([i])} />
            )}
          </div>
        )}
      </>
    );
    action = { kind: "place", space };
    if (tab === "major" && chosenImp) {
      action.improvement = chosenImp;
      if (upgrade) action.upgrade = true;
      if (bakeDict[chosenImp]) action.bake = { [chosenImp]: bakeDict[chosenImp] };
    } else if (tab === "minor") {
      const m = minorAction(chosenCard, cardParams);
      if (m) action.minor = m;
    }
    if (isReno && cells.length) action.stable = cells[0];
    const pickedSomething = (tab === "major" && chosenImp) || (tab === "minor" && chosenCard);
    disabled = isReno
      ? (chosenCard === "minor_shifting_cultivation" && cardParams?.cell === undefined)
      : !pickedSomething;
  } else if (space === "resource_market_3p") {
    title = "Resource Market";
    body = (
      <div style={{ display: "flex", gap: 8 }}>
        {["reed", "stone"].map((g) => (
          <Btn key={g} small variant={choice === g ? "primary" : "secondary"} onClick={() => setChoice(g)}>
            1 {GOODS[g].icon} {GOODS[g].label}
          </Btn>
        ))}
        <span style={{ fontSize: 12, alignSelf: "center" }}>+ 1 🍲</span>
      </div>
    );
    action = { kind: "place", space, choice };
  } else if (space.endsWith("_market")) {
    // Animal market with the Lasso option.
    title = "Animal market";
    body = (
      <label style={{ fontSize: 12 }}>
        <input type="checkbox" checked={useLasso}
          onChange={(e) => setUseLasso(e.target.checked)} />{" "}
        Use Lasso: place a second person immediately after this one
      </label>
    );
    action = { kind: "place", space };
    if (useLasso) action.lasso = true;
  }

  return (
    <PlannerShell title={title} onCancel={cancel} submitDisabled={disabled}
      onSubmit={() => submit(action)}>
      {error && <div style={{ color: "#dc2626", fontSize: 12, marginBottom: 6 }}>{error}</div>}
      {body}
    </PlannerShell>
  );
}

// ============================================================
// FEED DIALOG
// ============================================================

function FeedDialog({ me, state, foodNeeded, submit, error }) {
  const [conv, setConv] = useState({});  // key → count
  const cook = bestCook(me);
  const totals = animalTotals(me);

  const raw = rawValues(me);
  const options = [];
  options.push({ key: "grain_raw", label: `Grain → ${raw.grain} food`, good: "grain", via: "raw", value: raw.grain, max: me.resources.grain });
  options.push({ key: "veg_raw", label: `Vegetable → ${raw.vegetable} food`, good: "vegetable", via: "raw", value: raw.vegetable, max: me.resources.vegetable });
  if (cook) {
    options.push({ key: "veg_cook", label: `Cook vegetable → ${cook.vegetable} food`, good: "vegetable", via: "cook", value: cook.vegetable, max: me.resources.vegetable });
    for (const a of ANIMALS) {
      options.push({ key: `${a}_cook`, label: `Cook ${GOODS[a].label} ${GOODS[a].icon} → ${cook[a]} food`, good: a, via: "cook", value: cook[a], max: totals[a] });
    }
  }
  for (const [craft, [res, val]] of Object.entries(CRAFT_HARVEST)) {
    if (me.improvements.includes(craft) && !me.harvest_conversions_used.includes(craft)) {
      options.push({ key: craft, label: `${IMPROVEMENTS[craft].name}: 1 ${GOODS[res].label} → ${val} food`, good: res, via: craft, value: val, max: Math.min(1, me.resources[res]) });
    }
  }
  // Card-provided conversions ({give: {...}, get: {...}, per_harvest?}).
  for (const inst of inPlay(me)) {
    const convs = cardSpec(inst.id).conversions || [];
    convs.forEach((conv, i) => {
      const via = `${inst.id}:${i}`;
      const giveStr = Object.entries(conv.give).map(([g, n]) => `${n}${GOODS[g].icon}`).join(" ");
      const getStr = Object.entries(conv.get).map(([g, n]) => `${n}${GOODS[g].icon}`).join(" ");
      let max = 99;
      for (const [g, n] of Object.entries(conv.give)) {
        const have = ANIMALS.includes(g) ? totals[g] : me.resources[g];
        max = Math.min(max, Math.floor(have / n));
      }
      const used = me.harvest_conversions_used.filter((u) => u === via).length;
      if (conv.per_harvest != null) max = Math.min(max, conv.per_harvest - used);
      if (max > 0) {
        options.push({
          key: via, label: `${cardSpec(inst.id).name}: ${giveStr} → ${getStr}`,
          good: Object.keys(conv.give)[0], via, value: conv.get.food || 0, max,
        });
      }
    });
  }

  // Shared budgets (grain used by grain_raw only; veg by veg_raw + veg_cook).
  const vegUsed = (conv.veg_raw || 0) + (conv.veg_cook || 0);
  const foodGained = options.reduce((sum, o) => sum + (conv[o.key] || 0) * o.value, 0);
  const foodTotal = me.resources.food + foodGained;
  const shortfall = Math.max(0, foodNeeded - foodTotal);

  const doSubmit = () => {
    const conversions = options
      .filter((o) => (conv[o.key] || 0) > 0)
      .map((o) => ({ good: o.good, via: o.via, count: conv[o.key] }));
    submit({ kind: "feed", conversions });
  };

  return (
    <div style={{
      position: "fixed", inset: 0, background: "#00000066", zIndex: 50,
      display: "flex", alignItems: "center", justifyContent: "center",
    }}>
      <div style={{ background: "#fffbeb", borderRadius: 12, padding: 18, width: 440, maxHeight: "85vh", overflowY: "auto", fontFamily: FONT }}>
        <h3 style={{ margin: "0 0 8px" }}>Feeding phase</h3>
        {error && <div style={{ color: "#dc2626", fontSize: 12, marginBottom: 6 }}>{error}</div>}
        <div style={{ fontSize: 13, marginBottom: 8 }}>
          You need <b>{foodNeeded}</b> food. You have <b>{me.resources.food}</b> 🍲
          {foodGained > 0 && <> + <b>{foodGained}</b> from conversions = <b>{foodTotal}</b></>}.
        </div>
        <div style={{ display: "flex", flexDirection: "column", gap: 6, marginBottom: 10 }}>
          {options.map((o) => {
            let max = o.max;
            if (o.key === "veg_raw") max = me.resources.vegetable - (conv.veg_cook || 0);
            if (o.key === "veg_cook") max = me.resources.vegetable - (conv.veg_raw || 0);
            return (
              <div key={o.key} style={{ display: "flex", alignItems: "center", gap: 8, fontSize: 12 }}>
                <span style={{ minWidth: 210 }}>{o.label}</span>
                <Stepper value={conv[o.key] || 0} min={0} max={max}
                  onChange={(v) => setConv({ ...conv, [o.key]: v })} />
              </div>
            );
          })}
          {!options.some((o) => o.max > 0) && (
            <div style={{ fontSize: 12, color: "#57534e" }}>No conversions available.</div>
          )}
        </div>
        {shortfall > 0 && (
          <div style={{ color: "#dc2626", fontSize: 13, fontWeight: 700, marginBottom: 8 }}>
            ⚠ You are short {shortfall} food → {shortfall} begging marker(s) (−3 pts each)!
          </div>
        )}
        <Btn onClick={doSubmit}>
          {shortfall > 0 ? `Feed and beg (${shortfall})` : "Feed family"}
        </Btn>
      </div>
    </div>
  );
}

// ============================================================
// ACCOMMODATE DIALOG
// ============================================================

function AccommodateDialog({ me, gained, submit, error }) {
  const pastures = useMemo(() => computePastures(me.cells, me.fences), [me]);
  const stables = me.cells.map((c, i) => ({ c, i }))
    .filter(({ c, i }) => c.stable && c.type === "empty" && !pastures.some((p) => p.includes(i)))
    .map(({ i }) => i);
  const houseCap = houseCapacity(me);
  const pBonus = pastureBonus(me);

  // Pool: current farm animals + gained.
  const pool = animalTotals(me);
  for (const [a, n] of Object.entries(gained || {})) pool[a] += n;

  // Assignment state: pastures[i] → {type, count}; stables → {idx: type|null};
  // pets → {animal: count} in the house.
  const [pastureAssign, setPastureAssign] = useState(() =>
    pastures.map((p) => {
      // Start from current contents if they exist.
      let type = null, count = 0;
      for (const i of p) {
        if (me.cells[i].animal) { type = me.cells[i].animal.type; count += me.cells[i].animal.count; }
      }
      return { type, count };
    }));
  const [stableAssign, setStableAssign] = useState(() => {
    const out = {};
    for (const i of stables) out[i] = me.cells[i].animal ? me.cells[i].animal.type : null;
    return out;
  });
  const [pets, setPets] = useState({ ...(me.pets || {}) });
  const [cookN, setCookN] = useState({});
  const [discardN, setDiscardN] = useState({});
  const cook = bestCook(me);

  const placed = { sheep: 0, boar: 0, cattle: 0 };
  pastureAssign.forEach((a) => { if (a.type) placed[a.type] += a.count; });
  Object.values(stableAssign).forEach((t) => { if (t) placed[t] += 1; });
  for (const [a, n] of Object.entries(pets)) placed[a] += n;
  const petsTotal = Object.values(pets).reduce((x, y) => x + y, 0);
  const leftover = {};
  for (const a of ANIMALS) {
    leftover[a] = pool[a] - placed[a] - (cookN[a] || 0) - (discardN[a] || 0);
  }
  const balanced = ANIMALS.every((a) => leftover[a] === 0) && petsTotal <= houseCap;
  const overPlaced = ANIMALS.some((a) => leftover[a] < 0) || petsTotal > houseCap;

  const cycleType = (cur, allowNull = true) => {
    const order = allowNull ? [null, ...ANIMALS] : ANIMALS;
    return order[(order.indexOf(cur) + 1) % order.length];
  };

  const doSubmit = () => {
    const placements = [];
    pastures.forEach((p, i) => {
      const a = pastureAssign[i];
      if (a.type && a.count > 0) placements.push({ cell: p[0], type: a.type, count: a.count });
    });
    for (const [idx, t] of Object.entries(stableAssign)) {
      if (t) placements.push({ cell: +idx, type: t, count: 1 });
    }
    const act = { kind: "accommodate", placements,
                  pets: Object.fromEntries(Object.entries(pets).filter(([, v]) => v > 0)) };
    const cookOut = Object.fromEntries(Object.entries(cookN).filter(([, v]) => v > 0));
    const discOut = Object.fromEntries(Object.entries(discardN).filter(([, v]) => v > 0));
    if (Object.keys(cookOut).length) act.cook = cookOut;
    if (Object.keys(discOut).length) act.discard = discOut;
    submit(act);
  };

  return (
    <div style={{
      position: "fixed", inset: 0, background: "#00000066", zIndex: 50,
      display: "flex", alignItems: "center", justifyContent: "center",
    }}>
      <div style={{ background: "#fffbeb", borderRadius: 12, padding: 18, width: 480, maxHeight: "88vh", overflowY: "auto", fontFamily: FONT }}>
        <h3 style={{ margin: "0 0 8px" }}>Accommodate your animals</h3>
        {error && <div style={{ color: "#dc2626", fontSize: 12, marginBottom: 6 }}>{error}</div>}
        <div style={{ fontSize: 13, marginBottom: 8 }}>
          {Object.entries(gained || {}).filter(([, n]) => n > 0).length > 0 && (
            <>Gained: {Object.entries(gained).map(([a, n]) => `${n} ${GOODS[a].icon}`).join(", ")}. </>
          )}
          To place: {ANIMALS.filter((a) => pool[a] > 0).map((a) => `${pool[a]}${GOODS[a].icon}`).join(" ") || "none"}
        </div>

        {pastures.map((p, i) => {
          const cap = pastureCapacity(me.cells, p) + pBonus;
          const a = pastureAssign[i];
          return (
            <div key={i} style={{ display: "flex", alignItems: "center", gap: 8, fontSize: 12, marginBottom: 4 }}>
              <span style={{ minWidth: 170 }}>Pasture [{p.join(",")}] (cap {cap}):</span>
              <Btn small variant="secondary" onClick={() => {
                const next = [...pastureAssign];
                next[i] = { type: cycleType(a.type), count: a.type ? a.count : 1 };
                if (!next[i].type) next[i].count = 0;
                setPastureAssign(next);
              }}>{a.type ? GOODS[a.type].icon : "—"}</Btn>
              {a.type && (
                <Stepper value={a.count} min={1} max={cap} onChange={(v) => {
                  const next = [...pastureAssign];
                  next[i] = { ...a, count: v };
                  setPastureAssign(next);
                }} />
              )}
            </div>
          );
        })}
        {stables.map((idx) => (
          <div key={idx} style={{ display: "flex", alignItems: "center", gap: 8, fontSize: 12, marginBottom: 4 }}>
            <span style={{ minWidth: 170 }}>Unfenced stable (cell {idx}, cap 1):</span>
            <Btn small variant="secondary" onClick={() =>
              setStableAssign({ ...stableAssign, [idx]: cycleType(stableAssign[idx]) })
            }>{stableAssign[idx] ? GOODS[stableAssign[idx]].icon : "—"}</Btn>
          </div>
        ))}
        <div style={{ fontSize: 12, marginBottom: 8 }}>
          <div style={{ fontWeight: 700 }}>House pets (capacity {houseCap}):</div>
          {ANIMALS.map((a) => (
            <div key={a} style={{ display: "flex", alignItems: "center", gap: 8 }}>
              <span style={{ minWidth: 170 }}>{GOODS[a].icon} in the house</span>
              <Stepper value={pets[a] || 0} min={0} max={houseCap}
                onChange={(v) => setPets({ ...pets, [a]: v })} />
            </div>
          ))}
        </div>

        {cook && (
          <div style={{ marginBottom: 6 }}>
            <div style={{ fontSize: 12, fontWeight: 700 }}>Cook (Fireplace/Hearth):</div>
            {ANIMALS.filter((a) => pool[a] > 0).map((a) => (
              <div key={a} style={{ display: "flex", gap: 8, fontSize: 12, alignItems: "center" }}>
                <span style={{ minWidth: 170 }}>{GOODS[a].icon} → {cook[a]} food each</span>
                <Stepper value={cookN[a] || 0} min={0} max={pool[a]}
                  onChange={(v) => setCookN({ ...cookN, [a]: v })} />
              </div>
            ))}
          </div>
        )}
        <div style={{ marginBottom: 8 }}>
          <div style={{ fontSize: 12, fontWeight: 700 }}>Return to supply (discard):</div>
          {ANIMALS.filter((a) => pool[a] > 0).map((a) => (
            <div key={a} style={{ display: "flex", gap: 8, fontSize: 12, alignItems: "center" }}>
              <span style={{ minWidth: 170 }}>{GOODS[a].icon} discard</span>
              <Stepper value={discardN[a] || 0} min={0} max={pool[a]}
                onChange={(v) => setDiscardN({ ...discardN, [a]: v })} />
            </div>
          ))}
        </div>

        {!balanced && (
          <div style={{ color: overPlaced ? "#dc2626" : "#b45309", fontSize: 12, fontWeight: 700, marginBottom: 6 }}>
            {overPlaced ? "Too many animals assigned." :
              `Unassigned: ${ANIMALS.filter((a) => leftover[a] > 0).map((a) => `${leftover[a]}${GOODS[a].icon}`).join(" ")} — place, cook, or discard them.`}
          </div>
        )}
        <Btn onClick={doSubmit} disabled={!balanced}>Confirm</Btn>
      </div>
    </div>
  );
}

// ============================================================
// SCORE SHEET
// ============================================================

const SCORE_ROWS = [
  ["fields", "Fields"], ["pastures", "Pastures"], ["grain", "Grain"],
  ["vegetable", "Vegetables"], ["sheep", "Sheep"], ["boar", "Wild Boar"],
  ["cattle", "Cattle"], ["unused_spaces", "Unused spaces"],
  ["fenced_stables", "Fenced stables"], ["rooms", "Rooms"],
  ["people", "People"], ["improvements", "Improvements"], ["bonus", "Bonus/Begging"],
];

function ScoreSheet({ state }) {
  const scores = state.scores || [];
  return (
    <div style={{ background: "#fffbeb", border: "2px solid #d97706", borderRadius: 10, padding: 12, marginBottom: 10 }}>
      <h3 style={{ margin: "0 0 8px" }}>
        🏆 Final scores — winner: {state.winners.map((w) => state.players[w].name).join(", ")}
      </h3>
      <table style={{ borderCollapse: "collapse", fontSize: 12, width: "100%" }}>
        <thead>
          <tr>
            <th style={{ textAlign: "left", padding: 3 }}>Category</th>
            {scores.map((s) => <th key={s.player_index} style={{ padding: 3 }}>{s.name}</th>)}
          </tr>
        </thead>
        <tbody>
          {SCORE_ROWS.map(([key, label]) => (
            <tr key={key} style={{ borderTop: "1px solid #e7e5d8" }}>
              <td style={{ padding: 3 }}>{label}</td>
              {scores.map((s) => (
                <td key={s.player_index} style={{ padding: 3, textAlign: "center", color: s[key] < 0 ? "#dc2626" : "#292524" }}>
                  {s[key]}
                </td>
              ))}
            </tr>
          ))}
          <tr style={{ borderTop: "2px solid #d97706", fontWeight: 800 }}>
            <td style={{ padding: 3 }}>Total</td>
            {scores.map((s) => <td key={s.player_index} style={{ padding: 3, textAlign: "center" }}>{s.total}</td>)}
          </tr>
        </tbody>
      </table>
    </div>
  );
}

// ============================================================
// GAME BOARD
// ============================================================

const SIMPLE_SPACES = new Set([
  "grain_seeds", "vegetable_seeds", "day_laborer",
  "forest", "clay_pit", "reed_bank", "fishing", "copse", "grove",
  "hollow_3p", "hollow_4p", "traveling_players", "sheep_market",
  "pig_market", "cattle_market", "western_quarry", "eastern_quarry",
  "resource_market_4p", "urgent_wish",
]);
const MARKETS = new Set(["sheep_market", "pig_market", "cattle_market"]);

function GameBoard({ game }) {
  const { gameState: state, gameLogs, submitAction, error, playerId } = game;
  const [planner, setPlanner] = useState(null);
  const logRef = useRef(null);

  useEffect(() => {
    if (logRef.current) logRef.current.scrollTop = logRef.current.scrollHeight;
  }, [gameLogs]);
  useEffect(() => { setPlanner(null); }, [state?.current_player, state?.round, state?.phase]);

  if (!state || !state.players) {
    return <div style={{ padding: 40, fontFamily: FONT }}>Loading game…</div>;
  }

  const myIdx = state.your_player_idx;
  const me = myIdx !== null && myIdx !== undefined ? state.players[myIdx] : null;
  const validActions = state.valid_actions || [];
  const validSpaces = new Set(validActions.filter((a) => a.kind === "place").map((a) => a.space));
  const prompt = (state.prompts || [])[0];
  const promptMine = prompt && me && prompt.player === myIdx;
  const pendingMine = promptMine && prompt.type === "accommodate";
  const choiceMine = promptMine && prompt.type === "choice";
  const cardActions = validActions.filter((a) => a.kind === "card_action");
  const feedAction = validActions.find((a) => a.kind === "feed");
  const phase = game.phaseInfo || {};

  const pick = (spaceId) => {
    // Markets open a planner only when the Lasso is available.
    if (MARKETS.has(spaceId) && me && hasLasso(me)
        && me.people_total - me.people_placed >= 2) {
      setPlanner(spaceId);
      return;
    }
    if (SIMPLE_SPACES.has(spaceId)) submitAction({ kind: "place", space: spaceId });
    else setPlanner(spaceId);
  };

  return (
    <div style={{ minHeight: "100vh", background: "linear-gradient(160deg,#f7fee7,#ecfccb)", fontFamily: FONT, color: "#292524" }}>
      <div style={{ maxWidth: 1400, margin: "0 auto", padding: 14 }}>
        {/* Header */}
        <div style={{ display: "flex", alignItems: "center", gap: 14, marginBottom: 10, flexWrap: "wrap" }}>
          <h2 style={{ margin: 0, fontSize: 20 }}>🚜 Agricola</h2>
          <span style={{ fontSize: 13 }}>
            Round <b>{state.round}</b>/14 · Stage {state.stage}
            {HARVEST_ROUNDS.includes(state.round) && <span title="Harvest at the end of this round"> 🌾⏰</span>}
          </span>
          <span style={{ fontSize: 13, fontStyle: "italic", color: "#57534e" }}>{phase.description}</span>
          <span style={{ marginLeft: "auto", fontSize: 12 }}>
            Room {game.roomCode} {game.connected ? "🟢" : "🔴"}
          </span>
        </div>

        {state.game_over && <ScoreSheet state={state} />}

        {error && !planner && !pendingMine && !feedAction && (
          <div style={{ color: "#dc2626", fontSize: 13, marginBottom: 6 }}>{error}</div>
        )}
        {planner && me && (
          <Planner space={planner} state={state} me={me} error={error}
            actionInfo={validActions.find((a) => a.space === planner)}
            submit={(a) => { submitAction(a); setPlanner(null); }}
            cancel={() => setPlanner(null)} />
        )}
        {cardActions.length > 0 && !planner && (
          <div style={{ display: "flex", gap: 6, flexWrap: "wrap", marginBottom: 8 }}>
            {cardActions.map((a) => (
              <Btn key={a.card} small variant="secondary"
                onClick={() => submitAction({ kind: "card_action", card: a.card })}>
                ⚡ {cardSpec(a.card).name}: {a.description}
              </Btn>
            ))}
          </div>
        )}

        {/* Board row: the action board with the log beside it */}
        <div style={{ display: "flex", gap: 12, alignItems: "flex-start", flexWrap: "wrap" }}>
          <ActionBoard state={state} validSpaces={planner ? new Set() : validSpaces}
            onPick={pick} players={state.players} />
          <div style={{ flex: 1, minWidth: 220 }}>
            <div style={{ fontSize: 11, fontWeight: 800, color: "#57534e", textTransform: "uppercase", marginBottom: 4 }}>
              Game log
            </div>
            <div ref={logRef} style={{
              background: "#fefce8", border: "1px solid #d6d3c1", borderRadius: 8,
              padding: 8, height: 548, overflowY: "auto", fontSize: 11, lineHeight: 1.5,
            }}>
              {gameLogs.map((m, i) => (
                <div key={i} style={{
                  borderBottom: "1px solid #f5f5f0", padding: "2px 0",
                  fontWeight: m.startsWith("—") ? 800 : 400,
                }}>{m}</div>
              ))}
            </div>
          </div>
        </div>

        {/* Farms + hand below the board */}
        <div style={{ display: "flex", gap: 12, flexWrap: "wrap", marginTop: 12 }}>
          {state.players.map((p) => (
            <PlayerPanel key={p.index} player={p} color={PLAYER_COLORS[p.index]}
              isYou={p.index === myIdx}
              isCurrent={state.phase === "work" && state.current_player === p.index}
              isStarting={state.starting_player === p.index}
              state={state}>
              <FarmYard player={p} />
            </PlayerPanel>
          ))}
        </div>
        {me && <HandPanel me={me} playableMinors={state.playable_minors} />}
      </div>

      {/* Blocking dialogs */}
      {pendingMine && me && (
        <AccommodateDialog me={me} gained={prompt.gained} error={error}
          submit={submitAction} />
      )}
      {choiceMine && me && (
        <div style={{
          position: "fixed", inset: 0, background: "#00000066", zIndex: 50,
          display: "flex", alignItems: "center", justifyContent: "center",
        }}>
          <div style={{ background: "#fffbeb", borderRadius: 12, padding: 18, width: 400, fontFamily: FONT }}>
            <h3 style={{ margin: "0 0 8px" }}>{cardSpec(prompt.card).name}</h3>
            {error && <div style={{ color: "#dc2626", fontSize: 12, marginBottom: 6 }}>{error}</div>}
            <div style={{ fontSize: 13, marginBottom: 10 }}>{prompt.prompt}</div>
            <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
              {prompt.options.map((opt, i) => (
                <Btn key={i} onClick={() => submitAction({ kind: "choice", index: i })}>
                  {opt}
                </Btn>
              ))}
            </div>
          </div>
        </div>
      )}
      {feedAction && me && !pendingMine && !choiceMine && (
        <FeedDialog me={me} state={state} foodNeeded={feedAction.food_needed}
          error={error} submit={submitAction} />
      )}
    </div>
  );
}

// ============================================================
// LOBBY
// ============================================================

export const DECK_CHOICES = [
  { id: "A", label: "Deck A (revised base)" },
  { id: "B", label: "Deck B (Bubulcus)" },
  { id: "C", label: "Deck C (Corbarius)" },
  { id: "D", label: "Deck D (Dulcinaria)" },
  { id: "base", label: "Engine deck (classics)" },
  { id: "custom", label: "Custom cards" },
];

// Shared with the game-selector lobby (main.jsx) so a name entered anywhere
// prefills everywhere.
const NAME_KEY = "bge_player_name";

function Lobby({ game }) {
  const [name, setName] = useState(() =>
    localStorage.getItem(NAME_KEY) || sessionStorage.getItem("player_name") || "");
  const [code, setCode] = useState("");
  const [decks, setDecks] = useState(["A"]);
  const inRoom = !!game.roomCode;

  const S = {
    page: { minHeight: "100vh", background: "linear-gradient(160deg,#f7fee7,#d9f99d)", display: "flex", alignItems: "center", justifyContent: "center", fontFamily: FONT },
    card: { background: "#fffbeb", borderRadius: 14, padding: 28, width: 380, boxShadow: "0 8px 30px #3f621233" },
    input: { width: "100%", padding: "8px 10px", borderRadius: 6, border: "1px solid #d6d3c1", fontSize: 14, marginBottom: 10, fontFamily: "inherit", boxSizing: "border-box" },
  };

  // An auto create/join/reconnect handed off from the game-selector lobby is
  // in flight — show what's happening instead of a second entry form. On
  // error (room full, expired token, …) fall through to the form below.
  if (!inRoom && game.pendingIntent && !game.error) {
    const label = {
      create: "Creating room…",
      join: `Joining room ${game.pendingIntent.code || ""}…`,
      reconnect: "Reconnecting to your game…",
    }[game.pendingIntent.kind] || "Connecting…";
    return (
      <div style={S.page}>
        <div style={{ ...S.card, textAlign: "center" }}>
          <h2 style={{ marginTop: 0 }}>🚜 Agricola</h2>
          <div style={{ fontSize: 14, color: "#57534e" }}>{label}</div>
          <div style={{ marginTop: 14 }}>
            <a href="#" style={{ fontSize: 12, color: "#a8a29e" }}
              onClick={(e) => { e.preventDefault(); game.cancelPending(); }}>
              Taking too long? Enter details manually
            </a>
          </div>
        </div>
      </div>
    );
  }

  if (inRoom) {
    return (
      <div style={S.page}>
        <div style={S.card}>
          <h2 style={{ marginTop: 0 }}>🚜 Agricola</h2>
          <div style={{ fontSize: 14, marginBottom: 10 }}>
            Room code: <b style={{ fontSize: 20, letterSpacing: 2 }}>{game.roomCode}</b>
          </div>
          <div style={{ marginBottom: 14 }}>
            {game.lobby.map((p, i) => (
              <div key={p.player_id} style={{ display: "flex", alignItems: "center", gap: 8, padding: "4px 0", fontSize: 14 }}>
                <span style={{ width: 12, height: 12, borderRadius: "50%", background: PLAYER_COLORS[i % 4].bg }} />
                {p.name} {p.is_host && "👑"}
              </div>
            ))}
          </div>
          {game.isHost ? (
            <Btn onClick={game.startGame} disabled={game.lobby.length < 1 || game.lobby.length > 4}>
              Start game ({game.lobby.length} player{game.lobby.length === 1 ? " — solo" : "s"})
            </Btn>
          ) : (
            <div style={{ fontSize: 13, color: "#57534e" }}>Waiting for host to start…</div>
          )}
          {game.error && <div style={{ color: "#dc2626", fontSize: 12, marginTop: 8 }}>{game.error}</div>}
        </div>
      </div>
    );
  }

  return (
    <div style={S.page}>
      <div style={S.card}>
        <h2 style={{ marginTop: 0 }}>🚜 Agricola</h2>
        <p style={{ fontSize: 13, color: "#57534e" }}>
          17th-century farming: place your people, grow your farm, feed your family. 1–4 players.
        </p>
        <input style={S.input} placeholder="Your name" value={name}
          onChange={(e) => { setName(e.target.value); localStorage.setItem(NAME_KEY, e.target.value); }} />
        <div style={{ fontSize: 12, marginBottom: 8 }}>
          <b>Card decks:</b>
          <div style={{ display: "flex", flexDirection: "column", gap: 2, marginTop: 4 }}>
            {DECK_CHOICES.map((d) => (
              <label key={d.id}>
                <input type="checkbox" checked={decks.includes(d.id)}
                  onChange={(e) => setDecks(e.target.checked
                    ? [...decks, d.id] : decks.filter((x) => x !== d.id))} />
                {" "}{d.label}
              </label>
            ))}
          </div>
        </div>
        <div style={{ display: "flex", gap: 8, marginBottom: 12 }}>
          <Btn onClick={() => name.trim() && game.createRoom(name.trim(),
            decks.length ? { decks } : undefined)} disabled={!name.trim()}>
            Create room
          </Btn>
        </div>
        <div style={{ display: "flex", gap: 8 }}>
          <input style={{ ...S.input, marginBottom: 0, flex: 1 }} placeholder="Room code"
            value={code} onChange={(e) => setCode(e.target.value.toUpperCase())} maxLength={5} />
          <Btn variant="secondary" onClick={() => name.trim() && code.trim() && game.joinRoom(code.trim(), name.trim())}
            disabled={!name.trim() || !code.trim()}>
            Join
          </Btn>
        </div>
        {game.error && <div style={{ color: "#dc2626", fontSize: 12, marginTop: 8 }}>{game.error}</div>}
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
