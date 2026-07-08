import { useState, useRef, useCallback, useEffect, useMemo } from "react";

import { WS_URL } from "../ws.js";

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

function bestCook(player) {
  let cook = null;
  for (const imp of player.improvements) {
    const table = FIREPLACES.includes(imp) ? COOK_FIREPLACE : HEARTHS.includes(imp) ? COOK_HEARTH : null;
    if (!table) continue;
    if (!cook) cook = { ...table };
    else for (const k of Object.keys(table)) cook[k] = Math.max(cook[k], table[k]);
  }
  return cook;
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

  const createRoom = (name) => connect(() => send({ type: "create", game: "agricola", name }));
  const joinRoom = (code, name) => connect(() => send({ type: "join", room_code: code.toUpperCase(), name }));

  useEffect(() => {
    const pending = sessionStorage.getItem("pending_action");
    if (pending && !tokenRef.current) {
      try {
        const { roomCode: rc, playerName } = JSON.parse(pending);
        sessionStorage.removeItem("pending_action");
        if (rc) joinRoom(rc, playerName);
        else createRoom(playerName);
      } catch {
        sessionStorage.removeItem("pending_action");
      }
    }
  }, []);

  const startGame = () => send({ type: "start" });
  const submitAction = (action) => send({ type: "action", action });

  return {
    connected, roomCode, playerId, isHost, lobby,
    gameStarted, gameState, phaseInfo, yourTurn, waitingFor,
    gameLogs, gameOver, error,
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
        {player.pet && <span title="Pet" style={{ fontSize: 11 }}>🏠{GOODS[player.pet].icon}</span>}
        {player.begging > 0 && <span style={{ fontSize: 11, color: "#dc2626", fontWeight: 700 }}>🥺×{player.begging}</span>}
      </div>
      <div style={{ display: "flex", gap: 4, flexWrap: "wrap", marginBottom: 6 }}>
        {Object.keys(GOODS).filter((g) => !ANIMALS.includes(g)).map((g) => (
          <GoodChip key={g} good={g} count={player.resources[g]} small />
        ))}
        {ANIMALS.map((a) => <GoodChip key={a} good={a} count={totals[a]} small />)}
      </div>
      {children}
      {player.improvements.length > 0 && (
        <div style={{ display: "flex", gap: 4, flexWrap: "wrap", marginTop: 6 }}>
          {player.improvements.map((imp) => (
            <span key={imp} title={IMPROVEMENTS[imp].desc} style={{
              fontSize: 10, background: "#fecaca55", border: "1px solid #f87171",
              borderRadius: 6, padding: "1px 6px", fontWeight: 700, color: "#7f1d1d",
            }}>{IMPROVEMENTS[imp].name}</span>
          ))}
        </div>
      )}
    </div>
  );
}

// ============================================================
// ACTION BOARD
// ============================================================

function ActionBoard({ state, validSpaces, onPick, players }) {
  const groups = [
    { title: "Permanent", spaces: state.action_spaces.filter((s) => s.stage === 0) },
    { title: "Round cards", spaces: state.action_spaces.filter((s) => s.stage > 0) },
  ];
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
      {groups.map((g) => (
        <div key={g.title}>
          <div style={{ fontSize: 11, fontWeight: 800, color: "#57534e", textTransform: "uppercase", marginBottom: 4 }}>
            {g.title}
          </div>
          <div style={{ display: "grid", gridTemplateColumns: "repeat(2, 1fr)", gap: 6 }}>
            {g.spaces.map((sp) => {
              const occupant = sp.occupied_by !== null ? players[sp.occupied_by] : null;
              const occColor = occupant ? PLAYER_COLORS[occupant.index] : null;
              const valid = validSpaces.has(sp.id);
              return (
                <div key={sp.id}
                  onClick={valid ? () => onPick(sp.id) : undefined}
                  title={sp.desc}
                  style={{
                    background: occupant ? "#e7e5e4" : valid ? "#ecfccb" : "#fafaf9",
                    border: valid ? "2px solid #65a30d" : "1px solid #d6d3c1",
                    borderRadius: 8, padding: "6px 8px", cursor: valid ? "pointer" : "default",
                    opacity: occupant ? 0.75 : 1, minHeight: 44,
                  }}>
                  <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", gap: 4 }}>
                    <b style={{ fontSize: 12 }}>{sp.name}</b>
                    {occupant && (
                      <span title={occupant.name} style={{
                        width: 12, height: 12, borderRadius: "50%", flexShrink: 0,
                        background: occColor.bg, border: `2px solid ${occColor.light}`,
                      }} />
                    )}
                  </div>
                  <div style={{ fontSize: 10, color: "#57534e" }}>{sp.desc}</div>
                  <div style={{ display: "flex", gap: 3, flexWrap: "wrap", marginTop: 2 }}>
                    {Object.entries(sp.supply || {}).map(([good, count]) => (
                      <GoodChip key={good} good={good} count={count} small />
                    ))}
                  </div>
                </div>
              );
            })}
          </div>
        </div>
      ))}
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
  // sow: {cellIdx: "grain"|"vegetable"}
  const emptyFields = me.cells.map((c, i) => ({ c, i }))
    .filter(({ c }) => c.type === "field" && !c.crops).map(({ i }) => i);
  const used = { grain: 0, vegetable: 0 };
  Object.values(sow).forEach((crop) => used[crop]++);
  const cycle = (idx) => {
    const cur = sow[idx];
    const next = { ...sow };
    if (!cur) {
      if (me.resources.grain - used.grain > 0) next[idx] = "grain";
      else if (me.resources.vegetable - used.vegetable > 0) next[idx] = "vegetable";
      else return;
    } else if (cur === "grain") {
      if (me.resources.vegetable - used.vegetable > 0) next[idx] = "vegetable";
      else delete next[idx];
    } else delete next[idx];
    setSow(next);
  };
  if (!emptyFields.length) return <div style={{ fontSize: 12 }}>No empty fields to sow.</div>;
  return (
    <div style={{ fontSize: 12 }}>
      <div style={{ fontWeight: 700, marginBottom: 4 }}>
        Sow (click a field to cycle grain → vegetable → none):
      </div>
      <div style={{ display: "flex", gap: 6, flexWrap: "wrap" }}>
        {emptyFields.map((idx) => (
          <Btn key={idx} small variant="secondary" onClick={() => cycle(idx)}>
            Field {idx}: {sow[idx] ? GOODS[sow[idx]].icon : "—"}
          </Btn>
        ))}
      </div>
    </div>
  );
}

function ImprovementPicker({ state, me, chosen, setChosen, upgrade, setUpgrade }) {
  const ownsFireplace = me.improvements.some((i) => FIREPLACES.includes(i));
  const canAfford = (cost) => Object.entries(cost).every(([r, a]) => me.resources[r] >= a);
  return (
    <div style={{ display: "grid", gridTemplateColumns: "repeat(2,1fr)", gap: 6 }}>
      {state.available_improvements.map((imp) => {
        const spec = IMPROVEMENTS[imp];
        const affordable = canAfford(spec.cost);
        const upgradeable = spec.upgrade && ownsFireplace;
        const selectable = affordable || upgradeable;
        const selected = chosen === imp;
        return (
          <div key={imp}
            onClick={selectable ? () => { setChosen(selected ? null : imp); setUpgrade(!affordable && upgradeable); } : undefined}
            style={{
              border: selected ? "2px solid #d97706" : "1px solid #d6d3c1",
              background: selected ? "#fef3c7" : selectable ? "#fff" : "#f5f5f4",
              opacity: selectable ? 1 : 0.5, borderRadius: 8, padding: 6, cursor: selectable ? "pointer" : "default",
            }}>
            <div style={{ display: "flex", justifyContent: "space-between", fontSize: 12 }}>
              <b>{spec.name}</b><span>⭐{spec.points}</span>
            </div>
            <div style={{ display: "flex", gap: 3, margin: "2px 0" }}>
              {Object.entries(spec.cost).map(([g, n]) => <GoodChip key={g} good={g} count={n} small />)}
            </div>
            <div style={{ fontSize: 10, color: "#57534e" }}>{spec.desc}</div>
            {selected && spec.upgrade && ownsFireplace && (
              <label style={{ fontSize: 11, display: "block", marginTop: 4 }}
                onClick={(e) => e.stopPropagation()}>
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

function Planner({ space, state, me, submit, cancel, error }) {
  const [cells, setCells] = useState([]);         // rooms / stables / plow
  const [mode, setMode] = useState("rooms");      // farm_expansion toggle
  const [fences, setFences] = useState(new Set());
  const [sow, setSow] = useState({});
  const [bake, setBake] = useState({});
  const [chosenImp, setChosenImp] = useState(null);
  const [upgrade, setUpgrade] = useState(false);
  const [choice, setChoice] = useState("reed");

  const grainBudget = me.resources.grain - Object.values(sow).filter((c) => c === "grain").length;
  const sowList = Object.entries(sow).map(([cell, crop]) => ({ cell: +cell, crop }));
  const bakeDict = Object.fromEntries(Object.entries(bake).filter(([, v]) => v > 0));

  const toggleCell = (idx) => setCells(cells.includes(idx) ? cells.filter((c) => c !== idx) : [...cells, idx]);
  const toggleEdge = (edge) => {
    const next = new Set(fences);
    if (next.has(edge)) next.delete(edge); else next.add(edge);
    setFences(next);
  };

  const farmProps = { player: me, plannedFences: fences };

  let title = "", body = null, action = null, disabled = false;

  if (space === "farmland" || space === "cultivation") {
    title = space === "farmland" ? "Plow 1 field" : "Cultivation: plow and/or sow";
    const plowCell = cells[0];
    body = (
      <>
        <div style={{ fontSize: 12, marginBottom: 6 }}>Click an empty space to plow{space === "cultivation" ? " (optional)" : ""}.</div>
        <FarmYard {...farmProps} mode="cells" plannedCells={new Set(cells)}
          onCellClick={(i) => setCells(cells[0] === i ? [] : [i])} />
        {space === "cultivation" && <div style={{ marginTop: 8 }}><SowPlanner me={me} sow={sow} setSow={setSow} /></div>}
      </>
    );
    if (space === "farmland") {
      action = { kind: "place", space, cell: plowCell };
      disabled = plowCell === undefined;
    } else {
      action = { kind: "place", space, plow: plowCell ?? null, sow: sowList };
      if (plowCell === undefined) delete action.plow;
      disabled = plowCell === undefined && !sowList.length;
    }
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
  } else if (space === "side_job") {
    title = "Side Job: 1 stable (1 wood) and/or bake";
    const stableCell = cells[0];
    body = (
      <>
        <div style={{ fontSize: 12, marginBottom: 6 }}>Click a cell for the stable (optional):</div>
        <FarmYard {...farmProps} mode="cells" plannedCells={new Set(cells)}
          onCellClick={(i) => setCells(cells[0] === i ? [] : [i])} />
        <div style={{ marginTop: 8 }}>
          <BakePlanner me={me} bake={bake} setBake={setBake} grainBudget={grainBudget} />
        </div>
      </>
    );
    action = { kind: "place", space, bake: bakeDict };
    if (stableCell !== undefined) action.stable = stableCell;
    disabled = stableCell === undefined && !Object.keys(bakeDict).length;
  } else if (space === "fencing" || space === "farm_redevelopment") {
    const isReno = space === "farm_redevelopment";
    title = isReno ? "Farm Redevelopment: renovate, then fences" : "Build fences (1 wood each)";
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
      </>
    );
    action = { kind: "place", space };
    if (fences.size) action.fences = [...fences];
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
    title = isReno ? "House Redevelopment: renovate, then improvement" : "Build a major improvement";
    const spec = chosenImp ? IMPROVEMENTS[chosenImp] : null;
    body = (
      <>
        {isReno && (
          <div style={{ fontSize: 12, marginBottom: 6 }}>
            Renovates your house to {me.house_type === "wood" ? "clay" : "stone"}
            (1🌿 + 1 per room). Optionally also build an improvement:
          </div>
        )}
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
    );
    action = { kind: "place", space };
    if (chosenImp) {
      action.improvement = chosenImp;
      if (upgrade) action.upgrade = true;
      if (bakeDict[chosenImp]) action.bake = { [chosenImp]: bakeDict[chosenImp] };
    }
    disabled = isReno ? false : !chosenImp;
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

  const options = [];
  options.push({ key: "grain_raw", label: "Grain → 1 food", good: "grain", via: "raw", value: 1, max: me.resources.grain });
  options.push({ key: "veg_raw", label: "Vegetable → 1 food", good: "vegetable", via: "raw", value: 1, max: me.resources.vegetable });
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

  // Pool: current farm animals + gained.
  const pool = animalTotals(me);
  for (const [a, n] of Object.entries(gained || {})) pool[a] += n;

  // Assignment state: pastures[i] → {type, count}; stables → {idx: type|null}; pet.
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
  const [pet, setPet] = useState(me.pet);
  const [cookN, setCookN] = useState({});
  const [discardN, setDiscardN] = useState({});
  const cook = bestCook(me);

  const placed = { sheep: 0, boar: 0, cattle: 0 };
  pastureAssign.forEach((a) => { if (a.type) placed[a.type] += a.count; });
  Object.values(stableAssign).forEach((t) => { if (t) placed[t] += 1; });
  if (pet) placed[pet] += 1;
  const cooked = { ...cookN }, discarded = { ...discardN };
  const leftover = {};
  for (const a of ANIMALS) {
    leftover[a] = pool[a] - placed[a] - (cooked[a] || 0) - (discarded[a] || 0);
  }
  const balanced = ANIMALS.every((a) => leftover[a] === 0);
  const overPlaced = ANIMALS.some((a) => leftover[a] < 0);

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
    const act = { kind: "accommodate", placements, pet };
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
          const cap = pastureCapacity(me.cells, p);
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
        <div style={{ display: "flex", alignItems: "center", gap: 8, fontSize: 12, marginBottom: 8 }}>
          <span style={{ minWidth: 170 }}>Pet (in your house, cap 1):</span>
          <Btn small variant="secondary" onClick={() => setPet(cycleType(pet))}>
            {pet ? GOODS[pet].icon : "—"}
          </Btn>
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
  "meeting_place", "grain_seeds", "vegetable_seeds", "day_laborer",
  "forest", "clay_pit", "reed_bank", "fishing", "copse", "grove",
  "hollow_3p", "hollow_4p", "traveling_players", "sheep_market",
  "pig_market", "cattle_market", "western_quarry", "eastern_quarry",
  "resource_market_4p", "basic_wish", "urgent_wish",
]);

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
  const pendingMine = state.pending && me && state.pending.player === myIdx;
  const feedAction = validActions.find((a) => a.kind === "feed");
  const phase = game.phaseInfo || {};

  const pick = (spaceId) => {
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

        {/* Round track */}
        <div style={{ display: "flex", gap: 3, marginBottom: 12 }}>
          {Array.from({ length: 14 }, (_, i) => i + 1).map((r) => (
            <div key={r} title={HARVEST_ROUNDS.includes(r) ? "Harvest" : ""} style={{
              width: 26, height: 22, borderRadius: 4, fontSize: 11, fontWeight: 700,
              display: "flex", alignItems: "center", justifyContent: "center",
              background: r < state.round ? "#d6d3c1" : r === state.round ? "#65a30d" : "#fefce8",
              color: r === state.round ? "#fff" : "#57534e",
              border: HARVEST_ROUNDS.includes(r) ? "2px solid #ca8a04" : "1px solid #d6d3c1",
            }}>{r}</div>
          ))}
        </div>

        {state.game_over && <ScoreSheet state={state} />}

        <div style={{ display: "flex", gap: 12, alignItems: "flex-start", flexWrap: "wrap" }}>
          {/* Left: action board */}
          <div style={{ width: 330, flexShrink: 0 }}>
            <ActionBoard state={state} validSpaces={planner ? new Set() : validSpaces}
              onPick={pick} players={state.players} />
            {/* Major improvements supply */}
            <div style={{ marginTop: 10 }}>
              <div style={{ fontSize: 11, fontWeight: 800, color: "#57534e", textTransform: "uppercase", marginBottom: 4 }}>
                Major improvements available
              </div>
              <div style={{ display: "flex", gap: 4, flexWrap: "wrap" }}>
                {state.available_improvements.map((imp) => (
                  <span key={imp} title={`${IMPROVEMENTS[imp].desc} — cost: ${Object.entries(IMPROVEMENTS[imp].cost).map(([g, n]) => `${n} ${g}`).join(", ")}`}
                    style={{
                      fontSize: 10, background: "#fff", border: "1px solid #fca5a5",
                      borderRadius: 6, padding: "2px 6px", fontWeight: 700, color: "#7f1d1d",
                    }}>
                    {IMPROVEMENTS[imp].name} ⭐{IMPROVEMENTS[imp].points}
                  </span>
                ))}
              </div>
            </div>
          </div>

          {/* Center: planner + farms */}
          <div style={{ flex: 1, minWidth: 420 }}>
            {error && !planner && !pendingMine && !feedAction && (
              <div style={{ color: "#dc2626", fontSize: 13, marginBottom: 6 }}>{error}</div>
            )}
            {planner && me && (
              <Planner space={planner} state={state} me={me} error={error}
                submit={(a) => { submitAction(a); setPlanner(null); }}
                cancel={() => setPlanner(null)} />
            )}
            <div style={{ display: "flex", gap: 12, flexWrap: "wrap" }}>
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
          </div>

          {/* Right: log */}
          <div style={{ width: 250, flexShrink: 0 }}>
            <div style={{ fontSize: 11, fontWeight: 800, color: "#57534e", textTransform: "uppercase", marginBottom: 4 }}>
              Game log
            </div>
            <div ref={logRef} style={{
              background: "#fefce8", border: "1px solid #d6d3c1", borderRadius: 8,
              padding: 8, height: 520, overflowY: "auto", fontSize: 11, lineHeight: 1.5,
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
      </div>

      {/* Blocking dialogs */}
      {pendingMine && me && (
        <AccommodateDialog me={me} gained={state.pending.gained} error={error}
          submit={submitAction} />
      )}
      {feedAction && me && !pendingMine && (
        <FeedDialog me={me} state={state} foodNeeded={feedAction.food_needed}
          error={error} submit={submitAction} />
      )}
    </div>
  );
}

// ============================================================
// LOBBY
// ============================================================

function Lobby({ game }) {
  const [name, setName] = useState(sessionStorage.getItem("player_name") || "");
  const [code, setCode] = useState("");
  const inRoom = !!game.roomCode;

  const S = {
    page: { minHeight: "100vh", background: "linear-gradient(160deg,#f7fee7,#d9f99d)", display: "flex", alignItems: "center", justifyContent: "center", fontFamily: FONT },
    card: { background: "#fffbeb", borderRadius: 14, padding: 28, width: 380, boxShadow: "0 8px 30px #3f621233" },
    input: { width: "100%", padding: "8px 10px", borderRadius: 6, border: "1px solid #d6d3c1", fontSize: 14, marginBottom: 10, fontFamily: "inherit", boxSizing: "border-box" },
  };

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
          onChange={(e) => { setName(e.target.value); sessionStorage.setItem("player_name", e.target.value); }} />
        <div style={{ display: "flex", gap: 8, marginBottom: 12 }}>
          <Btn onClick={() => name.trim() && game.createRoom(name.trim())} disabled={!name.trim()}>
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
