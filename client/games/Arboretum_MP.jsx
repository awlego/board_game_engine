import { useState, useEffect, useRef, useCallback, useMemo } from "react";

// ─── CONFIGURATION ─────────────────────────────────────────────────
import { WS_URL } from "../ws.js";

// ─── CONSTANTS ─────────────────────────────────────────────────────

const SPECIES_INFO = {
  cassia:          { name: "Cassia",          color: "#f4d03f", light: "#fdf5d4" },
  blue_spruce:     { name: "Blue Spruce",     color: "#5dade2", light: "#d6eaf8" },
  dogwood:         { name: "Dogwood",         color: "#f5b7b1", light: "#fdedec" },
  jacaranda:       { name: "Jacaranda",       color: "#a569bd", light: "#e8daef" },
  maple:           { name: "Maple",           color: "#e74c3c", light: "#fce4e4" },
  oak:             { name: "Oak",             color: "#784212", light: "#f0e0d0" },
  cherry_blossom:  { name: "Cherry Blossom",  color: "#ff69b4", light: "#ffe0ef" },
  royal_poinciana: { name: "Royal Poinciana", color: "#e67e22", light: "#fdebd0" },
  tulip_poplar:    { name: "Tulip Poplar",    color: "#27ae60", light: "#d4efdf" },
  willow:          { name: "Willow",          color: "#76d7c4", light: "#d0ece7" },
};

const PHASE_STEPS = [
  { id: "draw1",   name: "Draw 1",  icon: "1" },
  { id: "draw2",   name: "Draw 2",  icon: "2" },
  { id: "place",   name: "Place",   icon: "3" },
  { id: "discard", name: "Discard", icon: "4" },
];

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
    backgroundImage: `repeating-linear-gradient(45deg, transparent, transparent 35px, rgba(39,174,96,0.02) 35px, rgba(39,174,96,0.02) 70px)`,
  },
  content: { position: "relative", zIndex: 1, maxWidth: 1100, margin: "0 auto", padding: "16px 20px" },
  card: {
    background: "linear-gradient(135deg, rgba(22,27,34,0.95) 0%, rgba(13,17,23,0.98) 100%)",
    border: "1px solid #30363d", borderRadius: 8, padding: 20, marginBottom: 16,
    boxShadow: "0 4px 20px rgba(0,0,0,0.5)",
  },
  cardTitle: {
    fontFamily: font, fontSize: 18, color: "#27ae60",
    marginBottom: 12, borderBottom: "1px solid #30363d", paddingBottom: 8,
  },
  btn: {
    fontFamily: font, fontSize: 14, padding: "8px 20px", borderRadius: 6,
    border: "1px solid #30363d",
    background: "linear-gradient(135deg, #21262d 0%, #161b22 100%)",
    color: "#e8d5a3", cursor: "pointer", transition: "all 0.2s", fontWeight: 600,
  },
  btnP: { background: "linear-gradient(135deg, #27ae60 0%, #1e8449 100%)", color: "#fff", border: "1px solid #27ae60" },
  dis: { opacity: 0.4, cursor: "not-allowed" },
  title: {
    fontFamily: font, fontSize: 36, fontWeight: 700, color: "#27ae60",
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
  return Array.isArray(state.players[0].hand) ? 0 : 1;
}

function cardLabel(card) {
  const info = SPECIES_INFO[card.species];
  return `${info?.name || card.species} ${card.value}`;
}

function speciesName(speciesId) {
  return SPECIES_INFO[speciesId]?.name || speciesId;
}

function speciesColor(speciesId) {
  return SPECIES_INFO[speciesId]?.color || "#888";
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
    if (wsRef.current?.readyState === WebSocket.OPEN) {
      wsRef.current.send(JSON.stringify(msg));
    }
  }, []);

  const connect = useCallback((onOpen) => {
    if (wsRef.current?.readyState === WebSocket.OPEN) {
      onOpen?.();
      return;
    }
    const ws = new WebSocket(WS_URL);
    wsRef.current = ws;

    ws.onopen = () => {
      setConnected(true);
      setError(null);
      if (tokenRef.current) {
        ws.send(JSON.stringify({ type: "reconnect", token: tokenRef.current }));
      }
      onOpen?.();
    };

    ws.onmessage = (evt) => {
      const msg = JSON.parse(evt.data);
      switch (msg.type) {
        case "created":
          setRoomCode(msg.room_code);
          setPlayerId(msg.player_id);
          setToken(msg.token);
          tokenRef.current = msg.token;
          sessionStorage.setItem("game_token", msg.token);
          ws.send(JSON.stringify({ type: "auth", token: msg.token }));
          break;
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
          setLobby(msg.players || []);
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
          setGameLogs(prev => [...prev, ...(msg.messages || [])]);
          break;
        case "action_error":
          setError(msg.message);
          setTimeout(() => setError(null), 4000);
          break;
        case "error":
          setError(msg.message);
          setTimeout(() => setError(null), 4000);
          break;
        case "game_over":
          setGameOver(true);
          break;
        case "chat":
          break;
      }
    };

    ws.onclose = () => {
      setConnected(false);
      setTimeout(() => {
        if (tokenRef.current) connect();
      }, 2000);
    };

    ws.onerror = () => setError("Connection error");
  }, []);


  const createRoom = useCallback((name) => {
    connect(() => {
      setTimeout(() => {
        wsRef.current?.send(JSON.stringify({ type: "create", game: "arboretum", name }));
      }, 100);
    });
  }, [connect]);

  const joinRoom = useCallback((code, name) => {
    connect(() => {
      setTimeout(() => {
        wsRef.current?.send(JSON.stringify({ type: "join", room_code: code.toUpperCase(), name }));
      }, 100);
    });
  }, [connect]);


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

  const startGame = useCallback(() => {
    send({ type: "start" });
  }, [send]);

  const sendAction = useCallback((action) => {
    send({ type: "action", action });
  }, [send]);

  return {
    connected, roomCode, playerId, token, isHost,
    lobby, gameStarted, gameState, phaseInfo,
    yourTurn, waitingFor, gameLogs, gameOver, error,
    createRoom, joinRoom, startGame, sendAction,
  };
}

// ─── CARD COMPONENTS ───────────────────────────────────────────────

function ArboretumCard({ card, onClick, selected, disabled, small, highlighted }) {
  const info = SPECIES_INFO[card.species] || { color: "#888", name: card.species };
  const w = small ? 52 : 64;
  const h = small ? 72 : 90;
  return (
    <div onClick={disabled ? undefined : onClick} style={{
      width: w, height: h, position: "relative",
      background: selected
        ? `linear-gradient(135deg, ${info.color}22, ${info.color}11)`
        : "linear-gradient(135deg, #1a1f25, #12161c)",
      border: selected ? "2px solid #27ae60" : highlighted ? "2px solid #f1c40f" : `1px solid ${info.color}55`,
      borderTop: `4px solid ${info.color}`,
      borderRadius: 6,
      display: "flex", flexDirection: "column", alignItems: "center", justifyContent: "center",
      cursor: disabled ? "default" : onClick ? "pointer" : "default",
      opacity: disabled ? 0.4 : 1,
      transition: "all 0.15s",
      boxShadow: selected ? "0 0 10px rgba(39,174,96,0.3)" : highlighted ? "0 0 10px rgba(241,196,15,0.3)" : "0 2px 4px rgba(0,0,0,0.3)",
      flexShrink: 0,
    }}>
      <span style={{ fontSize: small ? 20 : 26, fontWeight: 700, color: info.color, lineHeight: 1 }}>
        {card.value}
      </span>
      <span style={{
        fontSize: small ? 7 : 9, color: info.color, opacity: 0.9,
        textTransform: "uppercase", letterSpacing: 0.3, marginTop: 3,
        textAlign: "center", lineHeight: 1.1, padding: "0 2px",
      }}>
        {info.name}
      </span>
    </div>
  );
}

function EmptyCell({ onClick, highlighted, row, col }) {
  return (
    <div onClick={highlighted ? onClick : undefined} style={{
      width: 52, height: 72, borderRadius: 6,
      border: highlighted ? "2px dashed #27ae60" : "1px dashed #30363d33",
      background: highlighted ? "rgba(39,174,96,0.08)" : "transparent",
      cursor: highlighted ? "pointer" : "default",
      display: "flex", alignItems: "center", justifyContent: "center",
      transition: "all 0.15s",
      boxShadow: highlighted ? "0 0 8px rgba(39,174,96,0.2)" : "none",
    }}>
      {highlighted && (
        <span style={{ fontSize: 16, color: "#27ae6066" }}>+</span>
      )}
    </div>
  );
}

// ─── GRID COMPONENT ────────────────────────────────────────────────

function ArboretumGrid({ grid, validPlacements, onCellClick, title, isActive }) {
  // Compute bounding box of placed cards + 1-cell margin
  const keys = Object.keys(grid);
  const validSet = new Set((validPlacements || []).map(p => `${p[0]},${p[1]}`));

  let minR = 4, maxR = 4, minC = 4, maxC = 4;
  for (const key of keys) {
    const [r, c] = key.split(",").map(Number);
    minR = Math.min(minR, r);
    maxR = Math.max(maxR, r);
    minC = Math.min(minC, c);
    maxC = Math.max(maxC, c);
  }
  // Also include valid placements in the bounding box
  for (const pos of (validPlacements || [])) {
    minR = Math.min(minR, pos[0]);
    maxR = Math.max(maxR, pos[0]);
    minC = Math.min(minC, pos[1]);
    maxC = Math.max(maxC, pos[1]);
  }

  // Add 1-cell margin
  minR = Math.max(0, minR - 1);
  maxR = Math.min(8, maxR + 1);
  minC = Math.max(0, minC - 1);
  maxC = Math.min(8, maxC + 1);

  const rows = [];
  for (let r = minR; r <= maxR; r++) {
    const cells = [];
    for (let c = minC; c <= maxC; c++) {
      const key = `${r},${c}`;
      const card = grid[key];
      const isValid = validSet.has(key);
      cells.push(
        <div key={key}>
          {card ? (
            <ArboretumCard card={card} small disabled={false} />
          ) : (
            <EmptyCell
              row={r} col={c}
              highlighted={isValid}
              onClick={() => onCellClick?.(r, c)}
            />
          )}
        </div>
      );
    }
    rows.push(
      <div key={r} style={{ display: "flex", gap: 4, justifyContent: "center" }}>
        {cells}
      </div>
    );
  }

  return (
    <div style={{
      ...S.card, padding: 12,
      border: isActive ? "1px solid #27ae6044" : "1px solid #30363d",
    }}>
      {title && (
        <div style={{
          fontSize: 13, color: isActive ? "#27ae60" : "#6a604a", fontWeight: 600,
          marginBottom: 8, textTransform: "uppercase", letterSpacing: 1,
        }}>
          {title}
        </div>
      )}
      <div style={{ display: "flex", flexDirection: "column", gap: 4, alignItems: "center" }}>
        {rows.length > 0 ? rows : (
          <div style={{ padding: 20, color: "#6a604a", fontStyle: "italic", fontSize: 13 }}>
            No cards placed yet
          </div>
        )}
      </div>
    </div>
  );
}

// ─── DRAW AREA ─────────────────────────────────────────────────────

function DrawArea({ state, myIdx, yourTurn, phase, sendAction }) {
  const canDraw = yourTurn && (phase === "draw1" || phase === "draw2");

  return (
    <div style={{ ...S.card, padding: 12 }}>
      <div style={{
        fontSize: 12, color: "#6a604a", marginBottom: 8,
        textTransform: "uppercase", letterSpacing: 1,
      }}>
        Draw Sources
      </div>
      <div style={{ display: "flex", gap: 12, flexWrap: "wrap", alignItems: "flex-start" }}>
        {/* Draw pile */}
        <div
          onClick={canDraw && state.draw_pile > 0 ? () => sendAction({ kind: "draw_card", source: "deck" }) : undefined}
          style={{
            background: canDraw && state.draw_pile > 0 ? "rgba(39,174,96,0.1)" : "rgba(0,0,0,0.2)",
            border: canDraw && state.draw_pile > 0 ? "2px solid #27ae60" : "1px solid #30363d44",
            borderRadius: 6, padding: "10px 20px", textAlign: "center",
            cursor: canDraw && state.draw_pile > 0 ? "pointer" : "default",
            transition: "all 0.15s",
            minWidth: 80,
          }}
        >
          <div style={{ fontSize: 10, color: "#6a604a", textTransform: "uppercase", marginBottom: 4 }}>Deck</div>
          <div style={{ fontSize: 22, fontWeight: 700, color: "#e8d5a3" }}>{state.draw_pile}</div>
          <div style={{ fontSize: 9, color: "#6a604a" }}>cards</div>
        </div>

        {/* Per-player discard piles */}
        {state.players.map((player, pi) => {
          const topCard = player.discard?.length > 0 ? player.discard[player.discard.length - 1] : null;
          const canDrawThis = canDraw && topCard;
          const isMe = pi === myIdx;

          return (
            <div
              key={pi}
              onClick={canDrawThis ? () => sendAction({ kind: "draw_card", source: "discard", player_index: pi }) : undefined}
              style={{
                background: canDrawThis ? "rgba(39,174,96,0.1)" : "rgba(0,0,0,0.2)",
                border: canDrawThis ? "2px solid #27ae60" : "1px solid #30363d44",
                borderRadius: 6, padding: "8px 12px", textAlign: "center",
                cursor: canDrawThis ? "pointer" : "default",
                transition: "all 0.15s",
                minWidth: 80,
              }}
            >
              <div style={{ fontSize: 10, color: isMe ? "#27ae60" : "#6a604a", textTransform: "uppercase", marginBottom: 4 }}>
                {isMe ? "Your" : player.name + "'s"} Discard
              </div>
              {topCard ? (
                <ArboretumCard card={topCard} small disabled={!canDrawThis} />
              ) : (
                <div style={{ fontSize: 11, color: "#6a604a", fontStyle: "italic", padding: "8px 0" }}>Empty</div>
              )}
              <div style={{ fontSize: 9, color: "#6a604a", marginTop: 2 }}>
                {player.discard?.length || 0} cards
              </div>
            </div>
          );
        })}
      </div>
    </div>
  );
}

// ─── HAND DISPLAY ──────────────────────────────────────────────────

function HandDisplay({ hand, onCardClick, selectedIndex, disabled }) {
  return (
    <div style={{ ...S.card, padding: 12 }}>
      <div style={{ fontSize: 12, color: "#6a604a", marginBottom: 8, textTransform: "uppercase", letterSpacing: 1 }}>
        Your Hand ({hand.length} cards)
      </div>
      <div style={{ display: "flex", gap: 6, flexWrap: "wrap" }}>
        {hand.map((card, i) => (
          <ArboretumCard key={i} card={card}
            onClick={disabled ? undefined : () => onCardClick(i)}
            selected={selectedIndex === i}
            disabled={disabled} />
        ))}
        {hand.length === 0 && (
          <span style={{ fontSize: 13, color: "#6a604a", fontStyle: "italic" }}>No cards in hand</span>
        )}
      </div>
    </div>
  );
}

// ─── PLAYER GRID TABS ──────────────────────────────────────────────

function PlayerGridTabs({ state, myIdx, activeTab, onTabChange, validPlacements, onCellClick }) {
  const players = state.players;

  return (
    <div>
      {/* Tabs */}
      <div style={{ display: "flex", gap: 4, marginBottom: 8 }}>
        {players.map((player, pi) => {
          const isMe = pi === myIdx;
          const isActive = activeTab === pi;
          const handCount = isMe ? player.hand.length : (typeof player.hand === "number" ? player.hand : player.hand.length);
          return (
            <button key={pi} onClick={() => onTabChange(pi)} style={{
              ...S.btn,
              fontSize: 12, padding: "6px 16px",
              background: isActive
                ? (isMe ? "rgba(39,174,96,0.15)" : "rgba(201,168,76,0.15)")
                : "rgba(22,27,34,0.6)",
              border: isActive
                ? (isMe ? "1px solid #27ae60" : "1px solid #c9a84c")
                : "1px solid #30363d66",
              color: isActive
                ? (isMe ? "#27ae60" : "#c9a84c")
                : "#8b949e",
            }}>
              {isMe ? "Your Arboretum" : `${player.name}'s`}
              {!isMe && ` (${handCount})`}
            </button>
          );
        })}
      </div>

      {/* Active grid */}
      <ArboretumGrid
        grid={players[activeTab].grid}
        validPlacements={activeTab === myIdx ? validPlacements : []}
        onCellClick={activeTab === myIdx ? onCellClick : undefined}
        title={activeTab === myIdx ? "Your Arboretum" : `${players[activeTab].name}'s Arboretum`}
        isActive={activeTab === myIdx}
      />
    </div>
  );
}

// ─── PHASE INDICATOR ───────────────────────────────────────────────

function PhaseIndicator({ phase }) {
  return (
    <div style={{ display: "flex", gap: 3, alignItems: "center", marginBottom: 12 }}>
      {PHASE_STEPS.map((step, i) => {
        const isCurrent = step.id === phase;
        const isPast = PHASE_STEPS.findIndex(s => s.id === phase) > i;
        return (
          <div key={step.id} style={{ display: "flex", alignItems: "center", gap: 3 }}>
            <div style={{
              padding: "4px 12px", borderRadius: 6,
              background: isCurrent ? "rgba(39,174,96,0.15)" : "rgba(22,27,34,0.6)",
              border: isCurrent ? "2px solid #27ae60" : "1px solid #30363d66",
              opacity: isPast ? 0.4 : 1,
              display: "flex", alignItems: "center", gap: 5,
            }}>
              <span style={{
                fontSize: 11, width: 18, height: 18, borderRadius: "50%", display: "flex",
                alignItems: "center", justifyContent: "center",
                background: isCurrent ? "#27ae60" : "#30363d",
                color: isCurrent ? "#fff" : "#8b949e", fontWeight: 700,
              }}>
                {step.icon}
              </span>
              <span style={{ fontSize: 11, fontWeight: isCurrent ? 700 : 400, color: isCurrent ? "#27ae60" : "#8b949e" }}>
                {step.name}
              </span>
            </div>
            {i < PHASE_STEPS.length - 1 && <span style={{ color: "#30363d88", fontSize: 10 }}>›</span>}
          </div>
        );
      })}
    </div>
  );
}

// ─── SHARED UI COMPONENTS ──────────────────────────────────────────

function WaitingBanner({ name }) {
  return (
    <div style={{ textAlign: "center", padding: 12 }}>
      <div style={{ fontSize: 24, marginBottom: 6 }}>...</div>
      <p style={{ color: "#27ae60", fontSize: 15, fontWeight: 600, marginBottom: 4, margin: 0 }}>
        Waiting for {name}...
      </p>
      <div style={{ display: "flex", justifyContent: "center", gap: 6, marginTop: 10 }}>
        {[0, 1, 2].map(i => (
          <div key={i} style={{
            width: 8, height: 8, borderRadius: "50%", background: "#27ae60",
            animation: `pulse 1.4s ease-in-out ${i * 0.2}s infinite`, opacity: 0.3,
          }} />
        ))}
      </div>
      <style>{`@keyframes pulse { 0%, 80%, 100% { opacity: 0.3; transform: scale(1); } 40% { opacity: 1; transform: scale(1.3); } }`}</style>
    </div>
  );
}

function GameLog({ logs }) {
  const endRef = useRef(null);
  useEffect(() => { endRef.current?.scrollIntoView({ behavior: "smooth" }); }, [logs.length]);

  if (logs.length === 0) return null;
  const recent = logs.slice(-10);

  return (
    <div style={{
      background: "rgba(0,0,0,0.2)", border: "1px solid #30363d44", borderRadius: 6,
      padding: "8px 12px", maxHeight: 130, overflowY: "auto",
    }}>
      <div style={{ fontSize: 10, color: "#6a604a", textTransform: "uppercase", marginBottom: 4 }}>Game Log</div>
      {recent.map((msg, i) => (
        <p key={logs.length - recent.length + i} style={{ fontSize: 12, color: "#8b949e", margin: "2px 0", lineHeight: 1.4 }}>
          {msg}
        </p>
      ))}
      <div ref={endRef} />
    </div>
  );
}

// ─── ACTION PANEL ──────────────────────────────────────────────────

function ActionPanel({ state, myIdx, yourTurn, phase, sendAction, selectedHandIndex, setSelectedHandIndex }) {
  const currentPlayerName = state.players[state.current_player].name;

  if (!yourTurn) {
    return (
      <div style={{ ...S.card, padding: 16 }}>
        <WaitingBanner name={currentPlayerName} />
      </div>
    );
  }

  if (phase === "draw1" || phase === "draw2") {
    return (
      <div style={S.card}>
        <div style={S.cardTitle}>
          {phase === "draw1" ? "Draw First Card" : "Draw Second Card"}
        </div>
        <p style={{ color: "#8b949e", fontSize: 13, margin: "4px 0" }}>
          Click the deck or a discard pile above to draw a card.
        </p>
      </div>
    );
  }

  if (phase === "place") {
    const hand = state.players[myIdx].hand;
    const selectedCard = selectedHandIndex !== null ? hand[selectedHandIndex] : null;

    return (
      <div style={S.card}>
        <div style={S.cardTitle}>Place a Card</div>
        {!selectedCard ? (
          <p style={{ color: "#8b949e", fontSize: 13, margin: "4px 0" }}>
            Select a card from your hand, then click a highlighted cell on your grid.
          </p>
        ) : (
          <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
            <p style={{ color: "#27ae60", fontSize: 13, margin: "4px 0" }}>
              Placing {cardLabel(selectedCard)} — click a highlighted cell.
            </p>
            <button onClick={() => setSelectedHandIndex(null)} style={bs(false)}>Cancel</button>
          </div>
        )}
      </div>
    );
  }

  if (phase === "discard") {
    return (
      <div style={S.card}>
        <div style={S.cardTitle}>Discard a Card</div>
        <p style={{ color: "#8b949e", fontSize: 13, margin: "4px 0" }}>
          Click a card in your hand to discard it. Choose carefully — cards in hand affect scoring rights!
        </p>
      </div>
    );
  }

  return null;
}

// ─── SPECIES REFERENCE ─────────────────────────────────────────────

function SpeciesReference({ activeSpecies }) {
  const [open, setOpen] = useState(false);
  return (
    <div style={{ display: "inline-block", position: "relative" }}>
      <button onClick={() => setOpen(!open)}
        style={{ ...S.btn, fontSize: 11, padding: "3px 10px" }}>
        {open ? "Hide" : "?"} Species
      </button>
      {open && (
        <div style={{
          ...S.card, marginTop: 6, padding: 12, position: "absolute", zIndex: 10,
          width: 260, boxShadow: "0 8px 30px rgba(0,0,0,0.6)", right: 0,
        }}>
          <div style={{ fontSize: 11, color: "#6a604a", marginBottom: 6, textTransform: "uppercase" }}>Active Species</div>
          {(activeSpecies || []).map(s => (
            <div key={s.id} style={{ display: "flex", gap: 8, marginBottom: 3, alignItems: "center" }}>
              <div style={{ width: 12, height: 12, borderRadius: 2, background: s.color, flexShrink: 0 }} />
              <span style={{ color: "#e8d5a3", fontSize: 12 }}>{s.name}</span>
            </div>
          ))}
          <div style={{ borderTop: "1px solid #30363d", marginTop: 8, paddingTop: 6 }}>
            <div style={{ fontSize: 10, color: "#6a604a", marginBottom: 4 }}>Scoring</div>
            <p style={{ fontSize: 10, color: "#8b949e", margin: "2px 0", lineHeight: 1.4 }}>1 pt per card in path</p>
            <p style={{ fontSize: 10, color: "#8b949e", margin: "2px 0", lineHeight: 1.4 }}>x2 if path 4+ AND all same species</p>
            <p style={{ fontSize: 10, color: "#8b949e", margin: "2px 0", lineHeight: 1.4 }}>+1 if starts with value 1</p>
            <p style={{ fontSize: 10, color: "#8b949e", margin: "2px 0", lineHeight: 1.4 }}>+2 if ends with value 8</p>
          </div>
        </div>
      )}
    </div>
  );
}

// ─── LOBBY SCREEN ──────────────────────────────────────────────────

function LobbyScreen({ conn }) {
  const [mode, setMode] = useState(null);
  const [name, setName] = useState("");
  const [joinCode, setJoinCode] = useState("");

  if (!conn.roomCode) {
    return (
      <div style={S.content}>
        <div style={{ textAlign: "center", padding: "40px 0 24px", borderBottom: "2px solid #30363d", marginBottom: 30 }}>
          <h1 style={S.title}>ARBORETUM</h1>
          <p style={{ fontSize: 14, color: "#8b949e", marginTop: 8, fontStyle: "italic" }}>
            A card game of tree planting strategy for 2-4 players
          </p>
        </div>

        {conn.error && (
          <div style={{ ...S.card, border: "1px solid #e74c3c44", padding: 12, marginBottom: 16 }}>
            <p style={{ color: "#e74c3c", fontSize: 13, margin: 0 }}>{conn.error}</p>
          </div>
        )}

        <div style={{ ...S.card, maxWidth: 440, margin: "40px auto", textAlign: "center" }}>
          {!mode && (
            <>
              <h2 style={S.cardTitle}>Play Online</h2>
              <p style={{ color: "#8b949e", marginBottom: 24, lineHeight: 1.6, fontSize: 13 }}>
                Create a new game room or join an existing one with a room code.
              </p>
              <div style={{ display: "flex", gap: 12, justifyContent: "center" }}>
                <button onClick={() => setMode("create")} style={bs(true)}>Create Room</button>
                <button onClick={() => setMode("join")} style={bs(false)}>Join Room</button>
              </div>
            </>
          )}

          {mode === "create" && (
            <>
              <h2 style={S.cardTitle}>Create Game Room</h2>
              <div style={{ marginBottom: 16, textAlign: "left" }}>
                <label style={{ color: "#27ae60", fontSize: 13, display: "block", marginBottom: 6 }}>Your Name</label>
                <input value={name} onChange={e => setName(e.target.value)} placeholder="Enter your name"
                  style={S.input} onKeyDown={e => e.key === "Enter" && name.trim() && conn.createRoom(name.trim())} />
              </div>
              <div style={{ display: "flex", gap: 12, justifyContent: "center" }}>
                <button onClick={() => name.trim() && conn.createRoom(name.trim())} disabled={!name.trim()} style={bs(true, !name.trim())}>
                  Create
                </button>
                <button onClick={() => setMode(null)} style={bs(false)}>Back</button>
              </div>
            </>
          )}

          {mode === "join" && (
            <>
              <h2 style={S.cardTitle}>Join Game Room</h2>
              <div style={{ marginBottom: 16, textAlign: "left" }}>
                <label style={{ color: "#27ae60", fontSize: 13, display: "block", marginBottom: 6 }}>Room Code</label>
                <input value={joinCode} onChange={e => setJoinCode(e.target.value.toUpperCase())} placeholder="e.g. F6PYC"
                  maxLength={5} style={{ ...S.input, letterSpacing: 4, fontSize: 20, textAlign: "center", fontWeight: 700 }} />
              </div>
              <div style={{ marginBottom: 16, textAlign: "left" }}>
                <label style={{ color: "#27ae60", fontSize: 13, display: "block", marginBottom: 6 }}>Your Name</label>
                <input value={name} onChange={e => setName(e.target.value)} placeholder="Enter your name"
                  style={S.input} onKeyDown={e => e.key === "Enter" && name.trim() && joinCode.length >= 4 && conn.joinRoom(joinCode, name.trim())} />
              </div>
              <div style={{ display: "flex", gap: 12, justifyContent: "center" }}>
                <button onClick={() => conn.joinRoom(joinCode, name.trim())} disabled={!name.trim() || joinCode.length < 4}
                  style={bs(true, !name.trim() || joinCode.length < 4)}>Join</button>
                <button onClick={() => setMode(null)} style={bs(false)}>Back</button>
              </div>
            </>
          )}
        </div>
      </div>
    );
  }

  // Waiting room
  return (
    <div style={S.content}>
      <div style={{ textAlign: "center", padding: "30px 0 20px", borderBottom: "2px solid #30363d", marginBottom: 30 }}>
        <h1 style={{ ...S.title, fontSize: 28 }}>Game Lobby</h1>
      </div>

      {conn.error && (
        <div style={{ ...S.card, border: "1px solid #e74c3c44", padding: 12, marginBottom: 16 }}>
          <p style={{ color: "#e74c3c", fontSize: 13, margin: 0 }}>{conn.error}</p>
        </div>
      )}

      <div style={{ ...S.card, maxWidth: 440, margin: "0 auto", textAlign: "center" }}>
        <h2 style={S.cardTitle}>Room Code</h2>
        <div style={{
          fontSize: 48, fontWeight: 700, letterSpacing: 8, color: "#27ae60",
          background: "rgba(0,0,0,0.3)", borderRadius: 12, padding: "16px 24px",
          marginBottom: 20, fontFamily: "monospace", userSelect: "all",
        }}>
          {conn.roomCode}
        </div>
        <p style={{ color: "#8b949e", fontSize: 13, marginBottom: 20 }}>
          Share this code with other players
        </p>

        <div style={{ marginBottom: 20 }}>
          <h3 style={{ color: "#27ae60", fontSize: 16, marginBottom: 10 }}>Players ({conn.lobby.length}/4)</h3>
          {conn.lobby.map((p) => (
            <div key={p.player_id} style={{
              display: "flex", alignItems: "center", gap: 10,
              padding: "8px 16px", marginBottom: 4, borderRadius: 6,
              background: p.player_id === conn.playerId ? "rgba(39,174,96,0.08)" : "rgba(0,0,0,0.15)",
              border: p.player_id === conn.playerId ? "1px solid #27ae6044" : "1px solid transparent",
            }}>
              <div style={{ width: 10, height: 10, borderRadius: "50%", background: p.connected ? "#27ae60" : "#e74c3c" }} />
              <span style={{ color: "#e8d5a3", fontSize: 14, flex: 1, textAlign: "left" }}>{p.name}</span>
              {p.player_id === conn.playerId && <span style={{ fontSize: 10, color: "#27ae60" }}>you</span>}
            </div>
          ))}
        </div>

        {conn.isHost && (
          <button onClick={conn.startGame} disabled={conn.lobby.length < 2}
            style={bs(true, conn.lobby.length < 2)}>
            Start Game ({conn.lobby.length} players)
          </button>
        )}
        {!conn.isHost && (
          <p style={{ color: "#8b949e", fontSize: 13, fontStyle: "italic" }}>Waiting for host to start...</p>
        )}
      </div>
    </div>
  );
}

// ─── GAME SCREEN ───────────────────────────────────────────────────

function GameScreen({ conn }) {
  const state = conn.gameState;
  const yourTurn = conn.yourTurn;
  const sendAction = conn.sendAction;
  const myIdx = getMyPlayerIdx(state);
  const hand = state.players[myIdx].hand;
  const phase = state.phase;

  const [selectedHandIndex, setSelectedHandIndex] = useState(null);
  const [activeGridTab, setActiveGridTab] = useState(myIdx);

  // Reset selection on phase/turn change
  useEffect(() => {
    setSelectedHandIndex(null);
  }, [phase, state.turn_number]);

  // Switch to own grid on your turn
  useEffect(() => {
    if (yourTurn) setActiveGridTab(myIdx);
  }, [yourTurn, myIdx]);

  // Compute valid placements for place phase
  const validPlacements = useMemo(() => {
    if (!yourTurn || phase !== "place" || selectedHandIndex === null) return [];
    // Extract valid positions from game state
    // We compute client-side from grid rather than relying on action list
    const grid = state.players[myIdx].grid;
    const keys = Object.keys(grid);
    if (keys.length === 0) return [[4, 4]];

    const valid = new Set();
    for (const key of keys) {
      const [r, c] = key.split(",").map(Number);
      const neighbors = [[r-1,c],[r+1,c],[r,c-1],[r,c+1]];
      for (const [nr, nc] of neighbors) {
        const nkey = `${nr},${nc}`;
        if (!grid[nkey] && nr >= 0 && nr <= 8 && nc >= 0 && nc <= 8) {
          valid.add(`${nr},${nc}`);
        }
      }
    }
    return [...valid].map(k => k.split(",").map(Number));
  }, [yourTurn, phase, selectedHandIndex, state, myIdx]);

  // Handle grid cell click
  const handleCellClick = useCallback((row, col) => {
    if (!yourTurn || phase !== "place" || selectedHandIndex === null) return;
    sendAction({ kind: "place_card", card_index: selectedHandIndex, row, col });
    setSelectedHandIndex(null);
  }, [yourTurn, phase, selectedHandIndex, sendAction]);

  // Handle hand card click
  const handleHandCardClick = useCallback((index) => {
    if (!yourTurn) return;

    if (phase === "place") {
      setSelectedHandIndex(selectedHandIndex === index ? null : index);
    } else if (phase === "discard") {
      sendAction({ kind: "discard_card", card_index: index });
    }
  }, [phase, yourTurn, selectedHandIndex, sendAction]);

  // Hand is interactive during place and discard phases
  const handDisabled = !yourTurn || (phase !== "place" && phase !== "discard");

  return (
    <div style={S.content}>
      {/* Title bar */}
      <div style={{
        display: "flex", alignItems: "center", justifyContent: "space-between",
        borderBottom: "2px solid #30363d", paddingBottom: 10, marginBottom: 12,
      }}>
        <h1 style={{ ...S.title, fontSize: 24 }}>ARBORETUM</h1>
        <div style={{ display: "flex", alignItems: "center", gap: 14 }}>
          <span style={{ color: "#27ae60", fontSize: 14, fontWeight: 700 }}>Turn {state.turn_number + 1}</span>
          <span style={{
            fontSize: 12, padding: "3px 10px", borderRadius: 6, fontWeight: 600,
            background: yourTurn ? "rgba(39,174,96,0.15)" : "rgba(139,148,158,0.1)",
            color: yourTurn ? "#27ae60" : "#8b949e",
            border: yourTurn ? "1px solid #27ae6044" : "1px solid #30363d44",
          }}>
            {yourTurn ? "Your Turn" : `${state.players[state.current_player].name}'s Turn`}
          </span>
          <div style={{
            fontSize: 10, padding: "3px 8px", borderRadius: 4,
            background: conn.connected ? "rgba(39,174,96,0.15)" : "rgba(231,76,60,0.15)",
            color: conn.connected ? "#27ae60" : "#e74c3c",
            border: conn.connected ? "1px solid #27ae6044" : "1px solid #e74c3c44",
          }}>{conn.connected ? "Online" : "Reconnecting..."}</div>
          <SpeciesReference activeSpecies={state.active_species} />
        </div>
      </div>

      {/* Error banner */}
      {conn.error && (
        <div style={{ padding: "8px 16px", marginBottom: 10, borderRadius: 6, background: "rgba(231,76,60,0.12)", border: "1px solid #e74c3c44" }}>
          <span style={{ fontSize: 13, color: "#e74c3c" }}>{conn.error}</span>
        </div>
      )}

      {/* Phase indicator */}
      <PhaseIndicator phase={phase} />

      {/* Action panel */}
      <ActionPanel state={state} myIdx={myIdx} yourTurn={yourTurn} phase={phase}
        sendAction={sendAction} selectedHandIndex={selectedHandIndex}
        setSelectedHandIndex={setSelectedHandIndex} />

      {/* Draw area */}
      <DrawArea state={state} myIdx={myIdx} yourTurn={yourTurn} phase={phase}
        sendAction={sendAction} />

      {/* Grid tabs */}
      <PlayerGridTabs
        state={state} myIdx={myIdx}
        activeTab={activeGridTab} onTabChange={setActiveGridTab}
        validPlacements={validPlacements}
        onCellClick={handleCellClick}
      />

      {/* Hand */}
      <HandDisplay hand={hand} onCardClick={handleHandCardClick}
        selectedIndex={selectedHandIndex} disabled={handDisabled} />

      {/* Game log */}
      <GameLog logs={conn.gameLogs} />
    </div>
  );
}

// ─── GAME OVER SCREEN ──────────────────────────────────────────────

function GameOverScreen({ conn }) {
  const state = conn.gameState;
  const myIdx = getMyPlayerIdx(state);
  const scoring = state.scoring_results;
  const totals = scoring?.totals || [];
  const winners = scoring?.winners || [];
  const iWon = winners.includes(myIdx);

  return (
    <div style={S.content}>
      <div style={{ textAlign: "center", padding: "40px 0 24px", borderBottom: "2px solid #30363d", marginBottom: 30 }}>
        <h1 style={S.title}>Game Over</h1>
      </div>

      {/* Winner banner */}
      <div style={{
        ...S.card, textAlign: "center", padding: 30,
        border: iWon ? "2px solid #27ae60" : "2px solid #e74c3c",
        boxShadow: iWon ? "0 0 30px rgba(39,174,96,0.2)" : "0 0 30px rgba(231,76,60,0.2)",
      }}>
        <div style={{ fontSize: 48, marginBottom: 12 }}>{iWon ? "!" : "..."}</div>
        <h2 style={{ fontSize: 28, color: iWon ? "#27ae60" : "#e74c3c", margin: "0 0 8px" }}>
          {iWon ? "Victory!" : "Defeat"}
        </h2>
        <p style={{ fontSize: 16, color: "#e8d5a3", margin: "0 0 12px" }}>
          {winners.length === 1
            ? `${state.players[winners[0]].name} wins!`
            : `${winners.map(w => state.players[w].name).join(" & ")} tie!`
          }
        </p>

        {/* Score totals */}
        <div style={{ display: "flex", gap: 16, justifyContent: "center", flexWrap: "wrap" }}>
          {state.players.map((player, pi) => (
            <div key={pi} style={{
              padding: "12px 20px", borderRadius: 8,
              background: winners.includes(pi) ? "rgba(39,174,96,0.12)" : "rgba(0,0,0,0.2)",
              border: winners.includes(pi) ? "1px solid #27ae6044" : "1px solid #30363d44",
            }}>
              <div style={{ fontSize: 13, color: pi === myIdx ? "#27ae60" : "#e8d5a3", fontWeight: 600 }}>
                {player.name} {pi === myIdx ? "(you)" : ""}
              </div>
              <div style={{
                fontSize: 32, fontWeight: 700,
                color: winners.includes(pi) ? "#27ae60" : "#8b949e",
              }}>
                {totals[pi] || 0}
              </div>
              <div style={{ fontSize: 10, color: "#6a604a" }}>points</div>
            </div>
          ))}
        </div>
      </div>

      {/* Scoring Breakdown */}
      {scoring && <ScoringBreakdown scoring={scoring} players={state.players} myIdx={myIdx} activeSpecies={state.active_species} />}

      {/* Final grids */}
      <div style={{ ...S.card, padding: 12 }}>
        <div style={S.cardTitle}>Final Arboretums</div>
        <div style={{ display: "flex", gap: 16, flexWrap: "wrap" }}>
          {state.players.map((player, pi) => (
            <div key={pi} style={{ flex: 1, minWidth: 300 }}>
              <ArboretumGrid
                grid={player.grid}
                title={`${player.name}${pi === myIdx ? " (you)" : ""}`}
                isActive={pi === myIdx}
              />
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}

// ─── SCORING BREAKDOWN ─────────────────────────────────────────────

function ScoringBreakdown({ scoring, players, myIdx, activeSpecies }) {
  const [expandedSpecies, setExpandedSpecies] = useState(null);

  return (
    <div style={{ ...S.card, padding: 16 }}>
      <div style={S.cardTitle}>Scoring Breakdown</div>

      {/* Per-species summary table */}
      <div style={{ overflowX: "auto" }}>
        <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 13 }}>
          <thead>
            <tr style={{ borderBottom: "1px solid #30363d" }}>
              <th style={{ textAlign: "left", padding: "6px 8px", color: "#6a604a" }}>Species</th>
              {players.map((p, pi) => (
                <th key={pi} style={{
                  textAlign: "center", padding: "6px 8px",
                  color: pi === myIdx ? "#27ae60" : "#e8d5a3",
                }}>
                  {p.name}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {(scoring.species_results || []).map((sr, si) => {
              const speciesInfo = sr.species;
              const rights = sr.rights;
              const isExpanded = expandedSpecies === si;
              return [
                <tr key={si} onClick={() => setExpandedSpecies(isExpanded ? null : si)}
                  style={{ cursor: "pointer" }}>
                  <td style={{ padding: "6px 8px" }}>
                    <div style={{ display: "flex", alignItems: "center", gap: 6 }}>
                      <div style={{ width: 10, height: 10, borderRadius: 2, background: speciesInfo.color, flexShrink: 0 }} />
                      <span style={{ color: "#e8d5a3" }}>{speciesInfo.name}</span>
                      <span style={{ fontSize: 10, color: "#6a604a" }}>{isExpanded ? "▴" : "▾"}</span>
                    </div>
                  </td>
                  {sr.paths.map((path, pi) => (
                    <td key={pi} style={{
                      textAlign: "center", padding: "6px 8px",
                      color: path.has_rights ? (path.actual_score > 0 ? "#27ae60" : "#e8d5a3") : "#6a604a",
                      fontWeight: path.actual_score > 0 ? 700 : 400,
                    }}>
                      {path.actual_score}
                      {!path.has_rights && <span style={{ fontSize: 9, marginLeft: 2 }}>x</span>}
                    </td>
                  ))}
                </tr>,
                /* Hand-sum sub-row: always visible */
                <tr key={`${si}-hands`} onClick={() => setExpandedSpecies(isExpanded ? null : si)}
                  style={{ borderBottom: "1px solid #30363d22", cursor: "pointer" }}>
                  <td style={{ padding: "2px 8px 6px 26px", fontSize: 10, color: "#6a604a" }}>
                    hand
                  </td>
                  {players.map((player, pi) => {
                    const handCards = rights.hand_cards?.[pi] || [];
                    const adjusted = rights.adjusted_sums?.[pi] ?? 0;
                    const raw = rights.raw_sums?.[pi] ?? 0;
                    const hasRights = rights.eligible?.includes(pi);
                    const eightCancelled = rights.eights_cancelled?.[pi];
                    return (
                      <td key={pi} style={{ textAlign: "center", padding: "2px 8px 6px", verticalAlign: "top" }}>
                        <div style={{ display: "flex", gap: 2, justifyContent: "center", flexWrap: "wrap" }}>
                          {handCards.map((c, i) => (
                            <span key={i} style={{
                              padding: "0 3px", borderRadius: 3, fontSize: 10,
                              background: `${speciesColor(c.species)}22`,
                              color: speciesColor(c.species),
                              textDecoration: (c.value === 8 && eightCancelled) ? "line-through" : "none",
                            }}>
                              {c.value}
                            </span>
                          ))}
                          {handCards.length === 0 && <span style={{ color: "#6a604a", fontSize: 10 }}>—</span>}
                        </div>
                        <div style={{ fontSize: 10, marginTop: 1, color: hasRights ? "#27ae60" : "#6a604a" }}>
                          ={adjusted}{raw !== adjusted ? <span style={{ color: "#6a604a" }}>{` (was ${raw})`}</span> : ""}
                          {hasRights ? " ✓" : ""}
                        </div>
                      </td>
                    );
                  })}
                </tr>,
                isExpanded && (
                  <tr key={`${si}-detail`}>
                    <td colSpan={players.length + 1} style={{ padding: "8px 12px", background: "rgba(0,0,0,0.15)" }}>
                      <SpeciesDetail sr={sr} players={players} />
                    </td>
                  </tr>
                ),
              ];
            })}
            {/* Totals row */}
            <tr style={{ borderTop: "2px solid #30363d" }}>
              <td style={{ padding: "8px 8px", color: "#27ae60", fontWeight: 700 }}>Total</td>
              {players.map((_, pi) => (
                <td key={pi} style={{
                  textAlign: "center", padding: "8px 8px",
                  color: scoring.winners.includes(pi) ? "#27ae60" : "#e8d5a3",
                  fontWeight: 700, fontSize: 16,
                }}>
                  {scoring.totals[pi]}
                </td>
              ))}
            </tr>
          </tbody>
        </table>
      </div>
    </div>
  );
}

function SpeciesDetail({ sr, players }) {
  return (
    <div style={{ fontSize: 11 }}>
      {/* Path details */}
      <div>
        <span style={{ color: "#6a604a", textTransform: "uppercase", fontSize: 10 }}>Best paths</span>
        <div style={{ display: "flex", gap: 16, marginTop: 4, flexWrap: "wrap" }}>
          {sr.paths.map((path, pi) => (
            <div key={pi}>
              <span style={{ color: "#8b949e" }}>{players[pi].name}: </span>
              {path.path.length > 0 ? (
                <span style={{ color: path.has_rights ? "#27ae60" : "#6a604a" }}>
                  {path.path_score} pts ({path.path.length} cards)
                </span>
              ) : (
                <span style={{ color: "#6a604a" }}>no path</span>
              )}
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}

// ─── MAIN APP ──────────────────────────────────────────────────────

export default function App() {
  const conn = useGameConnection();
  const state = conn.gameState;

  // Not in a game yet
  if (!conn.gameStarted || !state) {
    return (
      <div style={S.app}>
        <div style={S.overlay} />
        <LobbyScreen conn={conn} />
      </div>
    );
  }

  // Game over
  if (conn.gameOver || state.game_over) {
    return (
      <div style={S.app}>
        <div style={S.overlay} />
        <GameOverScreen conn={conn} />
      </div>
    );
  }

  // Active game
  return (
    <div style={S.app}>
      <div style={S.overlay} />
      <GameScreen conn={conn} />
    </div>
  );
}
