import { useState, useEffect, useRef, useCallback, useMemo } from "react";

// ─── CONFIGURATION ─────────────────────────────────────────────────
import { WS_URL } from "../ws.js";

// ─── CONSTANTS ─────────────────────────────────────────────────────

const TROOP_COLORS = {
  red:    { hex: "#e74c3c", light: "#fce4e4", name: "Red" },
  blue:   { hex: "#3498db", light: "#daeaf6", name: "Blue" },
  yellow: { hex: "#f1c40f", light: "#fdf5d4", name: "Yellow" },
  green:  { hex: "#27ae60", light: "#d4efdf", name: "Green" },
  purple: { hex: "#8e44ad", light: "#e8d5f0", name: "Purple" },
  orange: { hex: "#e67e22", light: "#fdebd0", name: "Orange" },
};

const FORMATIONS = [
  { rank: 5, name: "Wedge",     desc: "Same color, consecutive values" },
  { rank: 4, name: "Phalanx",   desc: "All same value" },
  { rank: 3, name: "Battalion", desc: "Same color" },
  { rank: 2, name: "Skirmish",  desc: "Consecutive values" },
  { rank: 1, name: "Host",      desc: "Anything else" },
];

const TACTICS_INFO = {
  alexander:         { name: "Alexander",         icon: "👑", subtype: "leader",      desc: "Wild: any color, value 1-10" },
  darius:            { name: "Darius",            icon: "🏛️", subtype: "leader",      desc: "Wild: any color, value 1-10" },
  companion_cavalry: { name: "Companion Cavalry", icon: "🐴", subtype: "morale",      desc: "Wild: any color, value 8" },
  shield_bearers:    { name: "Shield Bearers",    icon: "🛡️", subtype: "morale",      desc: "Wild: any color, value 1-3" },
  fog:               { name: "Fog",               icon: "🌫️", subtype: "environment", desc: "Formation rank ignored, sum only" },
  mud:               { name: "Mud",               icon: "🏔️", subtype: "environment", desc: "4 cards required instead of 3" },
  scout:             { name: "Scout",             icon: "👁️", subtype: "guile",       desc: "Draw 3, return 2 to deck tops" },
  redeploy:          { name: "Redeploy",          icon: "🔄", subtype: "guile",       desc: "Move your card or discard it" },
  deserter:          { name: "Deserter",          icon: "🏃", subtype: "guile",       desc: "Remove opponent's card" },
  traitor:           { name: "Traitor",           icon: "🗡️", subtype: "guile",       desc: "Steal opponent's troop" },
};

const PHASE_STEPS = [
  { id: "play_card",   name: "Play Card",   icon: "🃏" },
  { id: "claim_flags", name: "Claim Flags", icon: "🏴" },
  { id: "draw_card",   name: "Draw Card",   icon: "📥" },
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
  return Array.isArray(state.players[0].hand) ? 0 : 1;
}

function cardLabel(card) {
  if (card.type === "troop") {
    const col = TROOP_COLORS[card.color];
    return `${col?.name || card.color} ${card.value}`;
  }
  const info = TACTICS_INFO[card.id];
  return info?.name || card.name || card.id;
}

function classifyFormation(cards, hasFog) {
  const troops = cards.filter(c => c.type === "troop");
  if (troops.length === 0) return { name: "-", rank: 0, sum: 0 };
  const values = troops.map(c => c.value);
  const colors = troops.map(c => c.color);
  const total = values.reduce((a, b) => a + b, 0);
  if (hasFog) return { name: "Host", rank: 1, sum: total };
  const sameColor = new Set(colors).size === 1;
  const sameValue = troops.length > 1 && new Set(values).size === 1;
  const sorted = [...values].sort((a, b) => a - b);
  const consecutive = sorted.length > 1 && sorted.every((v, i) => i === 0 || v === sorted[i - 1] + 1);
  const hasWild = cards.some(c => c.type === "tactics");
  if (sameColor && consecutive && !hasWild) return { name: "Wedge", rank: 5, sum: total };
  if (sameValue && !hasWild) return { name: "Phalanx", rank: 4, sum: total };
  if (sameColor) return { name: "Battalion", rank: 3, sum: total };
  if (consecutive && !hasWild) return { name: "Skirmish", rank: 2, sum: total };
  return { name: "Host", rank: 1, sum: total };
}

function getPlayActionKind(card) {
  if (card.type === "troop") return "play_troop";
  if (card.subtype === "leader" || card.subtype === "morale") return "play_morale_tactic";
  if (card.subtype === "environment") return "play_environment";
  if (card.id === "scout") return "play_scout";
  if (card.id === "redeploy") return "play_redeploy";
  if (card.id === "deserter") return "play_deserter";
  if (card.id === "traitor") return "play_traitor";
  return null;
}

function needsFlagTarget(card) {
  return card.type === "troop" || card.subtype === "leader" || card.subtype === "morale" || card.subtype === "environment";
}

function getValidFlags(state, myIdx, card) {
  const valid = [];
  for (let fi = 0; fi < 9; fi++) {
    const flag = state.flags[fi];
    if (flag.claimed_by !== null) continue;
    if (card.subtype === "environment") {
      if (!flag.environment.includes(card.id)) valid.push(fi);
    } else {
      const required = flag.environment.includes("mud") ? 4 : 3;
      if (flag.slots[myIdx].length < required) valid.push(fi);
    }
  }
  return valid;
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
        wsRef.current?.send(JSON.stringify({ type: "create", game: "battleline", name }));
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

function TroopCard({ card, onClick, selected, disabled, small }) {
  const col = TROOP_COLORS[card.color] || { hex: "#888", name: card.color };
  const w = small ? 48 : 60;
  const h = small ? 68 : 85;
  return (
    <div onClick={disabled ? undefined : onClick} style={{
      width: w, height: h, position: "relative",
      background: selected
        ? `linear-gradient(135deg, ${col.hex}22, ${col.hex}11)`
        : "linear-gradient(135deg, #1a1f25, #12161c)",
      border: selected ? "2px solid #c9a84c" : `1px solid ${col.hex}55`,
      borderLeft: `4px solid ${col.hex}`,
      borderRadius: 4,
      display: "flex", flexDirection: "column", alignItems: "center", justifyContent: "center",
      cursor: disabled ? "default" : onClick ? "pointer" : "default",
      opacity: disabled ? 0.4 : 1,
      transition: "all 0.15s",
      boxShadow: selected ? "0 0 10px rgba(201,168,76,0.3)" : "0 2px 4px rgba(0,0,0,0.3)",
      flexShrink: 0,
    }}>
      <span style={{ fontSize: small ? 18 : 24, fontWeight: 700, color: col.hex, lineHeight: 1 }}>{card.value}</span>
      <span style={{ fontSize: small ? 7 : 9, color: col.hex, opacity: 0.8, textTransform: "uppercase", letterSpacing: 0.5, marginTop: 2 }}>{col.name}</span>
    </div>
  );
}

function TacticsCard({ card, onClick, selected, disabled, small }) {
  const info = TACTICS_INFO[card.id] || { name: card.name || card.id, icon: "?", subtype: "?" };
  const w = small ? 48 : 60;
  const h = small ? 68 : 85;
  return (
    <div onClick={disabled ? undefined : onClick} style={{
      width: w, height: h,
      background: selected
        ? "linear-gradient(135deg, #c9a84c22, #c9a84c11)"
        : "linear-gradient(135deg, #1a1f25, #12161c)",
      border: selected ? "2px solid #c9a84c" : "1px solid #c9a84c55",
      borderRadius: 4,
      display: "flex", flexDirection: "column", alignItems: "center", justifyContent: "center", gap: 1,
      cursor: disabled ? "default" : onClick ? "pointer" : "default",
      opacity: disabled ? 0.4 : 1,
      transition: "all 0.15s",
      boxShadow: selected ? "0 0 10px rgba(201,168,76,0.3)" : "0 2px 4px rgba(0,0,0,0.3)",
      flexShrink: 0,
    }}>
      <span style={{ fontSize: small ? 14 : 18 }}>{info.icon}</span>
      <span style={{ fontSize: small ? 6 : 8, color: "#c9a84c", textAlign: "center", lineHeight: 1.1, fontWeight: 600, padding: "0 2px" }}>{info.name}</span>
      <span style={{ fontSize: small ? 5 : 7, color: "#6a604a", textTransform: "uppercase" }}>{info.subtype}</span>
    </div>
  );
}

function CardDisplay({ card, ...props }) {
  if (card.type === "troop") return <TroopCard card={card} {...props} />;
  return <TacticsCard card={card} {...props} />;
}

function CardBack({ small, cardType }) {
  const w = small ? 48 : 60;
  const h = small ? 68 : 85;
  const isTactics = cardType === "tactics";
  return (
    <div style={{
      width: w, height: h,
      background: isTactics
        ? "linear-gradient(135deg, #2a2318, #1c1710)"
        : "linear-gradient(135deg, #21262d, #161b22)",
      border: isTactics ? "1px solid #c9a84c44" : "1px solid #30363d",
      borderRadius: 4,
      display: "flex", flexDirection: "column", alignItems: "center", justifyContent: "center",
      flexShrink: 0,
      gap: 2,
    }}>
      <span style={{ fontSize: small ? 14 : 18, color: isTactics ? "#c9a84c55" : "#30363d" }}>
        {isTactics ? "🃏" : "⚔"}
      </span>
      <span style={{
        fontSize: small ? 5 : 7,
        color: isTactics ? "#c9a84c44" : "#30363d",
        textTransform: "uppercase", letterSpacing: 0.5,
      }}>
        {isTactics ? "tactics" : "troop"}
      </span>
    </div>
  );
}

// ─── BOARD COMPONENTS ──────────────────────────────────────────────

function FlagColumn({ flag, flagIndex, myIdx, onFlagClick, onCardClick, highlighted, isCardClickable, zoneHeight }) {
  const oppIdx = 1 - myIdx;
  const oppCards = flag.slots[oppIdx];
  const myCards = flag.slots[myIdx];
  const hasFog = flag.environment.includes("fog");
  const hasMud = flag.environment.includes("mud");

  const claimedByMe = flag.claimed_by === myIdx;
  const claimedByOpp = flag.claimed_by === oppIdx;
  const unclaimed = flag.claimed_by === null;

  const flagBg = claimedByMe ? "#27ae60" : claimedByOpp ? "#e74c3c" : "#30363d";
  const flagBorder = highlighted && unclaimed ? "#c9a84c" : flagBg;

  const myFormation = classifyFormation(myCards, hasFog);
  const oppFormation = classifyFormation(oppCards, hasFog);

  return (
    <div style={{
      display: "flex", flexDirection: "column", alignItems: "center",
      width: 58, flexShrink: 0,
    }}>
      {/* Opponent cards (top) */}
      <div style={{ display: "flex", flexDirection: "column", alignItems: "center", minHeight: zoneHeight, justifyContent: "flex-end" }}>
        {oppCards.map((card, ci) => {
          const clickable = isCardClickable?.(flagIndex, ci, oppIdx);
          return (
            <div key={ci} style={{ marginTop: ci > 0 ? 4 : 0, zIndex: ci, position: "relative" }}>
              <CardDisplay card={card} small
                onClick={clickable ? () => onCardClick(flagIndex, ci, oppIdx) : undefined}
                selected={clickable} disabled={false} />
            </div>
          );
        })}
        {oppCards.length > 0 && (
          <span style={{ fontSize: 7, color: "#6a604a", marginTop: 2 }}>
            {oppFormation.name} ({oppFormation.sum})
          </span>
        )}
      </div>

      {/* Flag marker */}
      <div
        onClick={highlighted && unclaimed ? () => onFlagClick(flagIndex) : undefined}
        style={{
          width: 36, height: 36, borderRadius: "50%",
          background: flagBg, border: `2px solid ${flagBorder}`,
          display: "flex", flexDirection: "column", alignItems: "center", justifyContent: "center",
          margin: "4px 0",
          cursor: highlighted && unclaimed ? "pointer" : "default",
          boxShadow: highlighted && unclaimed ? "0 0 12px rgba(201,168,76,0.5)" : "none",
          transition: "all 0.2s",
        }}
      >
        <span style={{ fontSize: 12, fontWeight: 700, color: "#fff", lineHeight: 1 }}>{flagIndex + 1}</span>
        {claimedByMe && <span style={{ fontSize: 7, lineHeight: 1 }}>✓</span>}
        {claimedByOpp && <span style={{ fontSize: 7, lineHeight: 1 }}>✗</span>}
      </div>

      {/* Environment badges */}
      {(hasFog || hasMud) && (
        <div style={{ display: "flex", gap: 1, marginBottom: 2 }}>
          {hasFog && <span style={{ fontSize: 9 }} title="Fog">🌫️</span>}
          {hasMud && <span style={{ fontSize: 9 }} title="Mud">🏔️</span>}
        </div>
      )}

      {/* Your cards (bottom) */}
      <div style={{ display: "flex", flexDirection: "column", alignItems: "center", minHeight: zoneHeight, justifyContent: "flex-start" }}>
        {myCards.map((card, ci) => {
          const clickable = isCardClickable?.(flagIndex, ci, myIdx);
          return (
            <div key={ci} style={{ marginTop: ci > 0 ? 4 : 0, zIndex: ci, position: "relative" }}>
              <CardDisplay card={card} small
                onClick={clickable ? () => onCardClick(flagIndex, ci, myIdx) : undefined}
                selected={clickable} disabled={false} />
            </div>
          );
        })}
        {myCards.length > 0 && (
          <span style={{ fontSize: 7, color: "#6a604a", marginTop: 2 }}>
            {myFormation.name} ({myFormation.sum})
          </span>
        )}
      </div>
    </div>
  );
}

function BoardDisplay({ state, myIdx, onFlagClick, onCardClick, highlightedFlags, isCardClickable }) {
  const oppIdx = 1 - myIdx;
  const opp = state.players[oppIdx];
  const me = state.players[myIdx];
  const oppHand = Array.isArray(opp.hand) ? opp.hand : [];
  const oppHandCount = oppHand.length;
  const anyMud = state.flags.some(f => f.environment.includes("mud"));
  const zoneHeight = anyMud ? 300 : 230;

  return (
    <div style={{ ...S.card, padding: 12 }}>
      {/* Opponent info */}
      <div style={{
        display: "flex", justifyContent: "space-between", alignItems: "center",
        marginBottom: 4, paddingBottom: 6, borderBottom: "1px solid #30363d44",
      }}>
        <span style={{ fontSize: 13, color: "#e8d5a3", fontWeight: 600 }}>
          {opp.name} ({oppHandCount} cards)
        </span>
        <span style={{ fontSize: 11, color: "#6a604a" }}>
          Tactics played: {opp.tactics_played}
        </span>
      </div>

      {/* Opponent hand (face down) */}
      {oppHandCount > 0 && (
        <div style={{
          display: "flex", justifyContent: "center", gap: 4,
          flexWrap: "wrap", marginBottom: 8, padding: "4px 0",
        }}>
          {oppHand.map((card, i) => (
            <CardBack key={i} small cardType={card.type} />
          ))}
        </div>
      )}

      {/* Flags row */}
      <div style={{
        display: "flex", justifyContent: "center", gap: 6,
        overflowX: "auto", padding: "8px 0",
      }}>
        {state.flags.map((flag, fi) => (
          <FlagColumn key={fi} flag={flag} flagIndex={fi} myIdx={myIdx}
            onFlagClick={onFlagClick} onCardClick={onCardClick}
            highlighted={highlightedFlags.has(fi)}
            isCardClickable={isCardClickable}
            zoneHeight={zoneHeight} />
        ))}
      </div>

      {/* Your info */}
      <div style={{
        display: "flex", justifyContent: "space-between", alignItems: "center",
        marginTop: 8, paddingTop: 6, borderTop: "1px solid #30363d44",
      }}>
        <span style={{ fontSize: 13, color: "#c9a84c", fontWeight: 600 }}>
          {me.name} (you)
        </span>
        <span style={{ fontSize: 11, color: "#6a604a" }}>
          Tactics played: {me.tactics_played}
        </span>
      </div>
    </div>
  );
}

// ─── HAND & DECK COMPONENTS ───────────────────────────────────────

function HandDisplay({ hand, onCardClick, selectedIndex, disabled }) {
  return (
    <div style={{ ...S.card, padding: 12 }}>
      <div style={{ fontSize: 12, color: "#6a604a", marginBottom: 8, textTransform: "uppercase", letterSpacing: 1 }}>
        Your Hand
      </div>
      <div style={{ display: "flex", gap: 6, flexWrap: "wrap" }}>
        {hand.map((card, i) => (
          <CardDisplay key={i} card={card}
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

function DeckArea({ troopCount, tacticsCount, discards }) {
  const [showDiscard, setShowDiscard] = useState(false);
  const allDiscards = [...(discards[0] || []), ...(discards[1] || [])];

  return (
    <div style={{ display: "flex", gap: 12, alignItems: "flex-start", flexWrap: "wrap" }}>
      {/* Troop deck */}
      <div style={{
        background: "rgba(0,0,0,0.2)", border: "1px solid #30363d44", borderRadius: 6,
        padding: "8px 16px", textAlign: "center",
      }}>
        <div style={{ fontSize: 10, color: "#6a604a", textTransform: "uppercase", marginBottom: 4 }}>Troop Deck</div>
        <div style={{ fontSize: 20, fontWeight: 700, color: "#e8d5a3" }}>{troopCount}</div>
      </div>

      {/* Tactics deck */}
      <div style={{
        background: "rgba(0,0,0,0.2)", border: "1px solid #30363d44", borderRadius: 6,
        padding: "8px 16px", textAlign: "center",
      }}>
        <div style={{ fontSize: 10, color: "#6a604a", textTransform: "uppercase", marginBottom: 4 }}>Tactics Deck</div>
        <div style={{ fontSize: 20, fontWeight: 700, color: "#c9a84c" }}>{tacticsCount}</div>
      </div>

      {/* Discard pile */}
      {allDiscards.length > 0 && (
        <div>
          <button onClick={() => setShowDiscard(!showDiscard)}
            style={{ ...S.btn, fontSize: 11, padding: "4px 10px" }}>
            Discards ({allDiscards.length}) {showDiscard ? "▴" : "▾"}
          </button>
          {showDiscard && (
            <div style={{ display: "flex", gap: 4, flexWrap: "wrap", marginTop: 6 }}>
              {allDiscards.map((card, i) => (
                <CardDisplay key={i} card={card} small disabled />
              ))}
            </div>
          )}
        </div>
      )}
    </div>
  );
}

// ─── SHARED UI COMPONENTS ─────────────────────────────────────────

function PhaseIndicator({ phase, subPhase }) {
  const activePhase = subPhase ? null : phase;
  const isSubPhase = !!subPhase;

  return (
    <div style={{ display: "flex", gap: 3, alignItems: "center", marginBottom: 12 }}>
      {PHASE_STEPS.map((step, i) => {
        const isCurrent = step.id === activePhase;
        const isPast = !isSubPhase && PHASE_STEPS.findIndex(s => s.id === activePhase) > i;
        return (
          <div key={step.id} style={{ display: "flex", alignItems: "center", gap: 3 }}>
            <div style={{
              padding: "4px 12px", borderRadius: 6,
              background: isCurrent ? "rgba(201,168,76,0.15)" : "rgba(22,27,34,0.6)",
              border: isCurrent ? "2px solid #c9a84c" : "1px solid #30363d66",
              opacity: isPast ? 0.4 : 1,
              display: "flex", alignItems: "center", gap: 5,
            }}>
              <span style={{ fontSize: 13 }}>{step.icon}</span>
              <span style={{ fontSize: 11, fontWeight: isCurrent ? 700 : 400, color: isCurrent ? "#c9a84c" : "#8b949e" }}>
                {step.name}
              </span>
            </div>
            {i < PHASE_STEPS.length - 1 && <span style={{ color: "#30363d88", fontSize: 10 }}>›</span>}
          </div>
        );
      })}
      {isSubPhase && (
        <div style={{
          padding: "4px 12px", borderRadius: 6, marginLeft: 4,
          background: "rgba(201,168,76,0.15)", border: "2px solid #c9a84c",
          display: "flex", alignItems: "center", gap: 5,
        }}>
          <span style={{ fontSize: 11, fontWeight: 700, color: "#c9a84c" }}>
            {subPhase.replace(/_/g, " ")}
          </span>
        </div>
      )}
    </div>
  );
}

function WaitingBanner({ name }) {
  return (
    <div style={{ textAlign: "center", padding: 12 }}>
      <div style={{ fontSize: 24, marginBottom: 6 }}>⏳</div>
      <p style={{ color: "#c9a84c", fontSize: 15, fontWeight: 600, marginBottom: 4, margin: 0 }}>
        Waiting for {name}...
      </p>
      <div style={{ display: "flex", justifyContent: "center", gap: 6, marginTop: 10 }}>
        {[0, 1, 2].map(i => (
          <div key={i} style={{
            width: 8, height: 8, borderRadius: "50%", background: "#c9a84c",
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
  const recent = logs.slice(-8);

  return (
    <div style={{
      background: "rgba(0,0,0,0.2)", border: "1px solid #30363d44", borderRadius: 6,
      padding: "8px 12px", maxHeight: 110, overflowY: "auto",
    }}>
      <div style={{ fontSize: 10, color: "#6a604a", textTransform: "uppercase", marginBottom: 4 }}>Game Log</div>
      {recent.map((msg, i) => (
        <p key={logs.length - recent.length + i} style={{ fontSize: 12, color: "#8b949e", margin: "2px 0", lineHeight: 1.4 }}>• {msg}</p>
      ))}
      <div ref={endRef} />
    </div>
  );
}

function FormationReference() {
  const [open, setOpen] = useState(false);
  return (
    <div style={{ display: "inline-block" }}>
      <button onClick={() => setOpen(!open)}
        style={{ ...S.btn, fontSize: 11, padding: "3px 10px" }}>
        {open ? "Hide" : "?"} Formations
      </button>
      {open && (
        <div style={{
          ...S.card, marginTop: 6, padding: 12, position: "absolute", zIndex: 10,
          width: 340, boxShadow: "0 8px 30px rgba(0,0,0,0.6)",
        }}>
          {FORMATIONS.map(f => (
            <div key={f.rank} style={{ display: "flex", gap: 8, marginBottom: 4, alignItems: "baseline" }}>
              <span style={{ color: "#c9a84c", fontWeight: 700, width: 16, fontSize: 13, textAlign: "right" }}>{f.rank}</span>
              <span style={{ color: "#e8d5a3", fontWeight: 600, width: 80, fontSize: 13 }}>{f.name}</span>
              <span style={{ color: "#8b949e", fontSize: 11 }}>{f.desc}</span>
            </div>
          ))}
          <p style={{ color: "#6a604a", fontSize: 10, marginTop: 8, marginBottom: 0 }}>
            Ties: higher sum wins, then who completed first.
          </p>
        </div>
      )}
    </div>
  );
}

// ─── ACTION PANEL ──────────────────────────────────────────────────

function ActionPanel({ state, myIdx, yourTurn, sendAction, selectedHandIndex, setSelectedHandIndex }) {
  const phase = state.phase;
  const sub = state.sub_phase;
  const currentPlayerName = state.players[state.current_player].name;

  if (!yourTurn) {
    return (
      <div style={{ ...S.card, padding: 16 }}>
        <WaitingBanner name={currentPlayerName} />
      </div>
    );
  }

  const hand = state.players[myIdx].hand;
  const selectedCard = selectedHandIndex !== null ? hand[selectedHandIndex] : null;

  // ── Auto-claim toggle (shared across phases) ──
  const autoClaimToggle = (
    <div style={{ marginTop: 8, display: "flex", alignItems: "center", gap: 6 }}>
      <label style={{ fontSize: 11, color: "#6a604a", cursor: "pointer", display: "flex", alignItems: "center", gap: 4 }}>
        <input type="checkbox" checked={state.auto_claim !== false}
          onChange={() => sendAction({ kind: "toggle_auto_claim" })} />
        Auto-claim flags
      </label>
    </div>
  );

  // ── Play Card Phase ──
  if (phase === "play_card") {
    const isGuile = selectedCard?.subtype === "guile";
    const hasTroops = hand.some(c => c.type === "troop");
    const hasOpenFlags = state.flags.some(f =>
      f.claimed_by === null &&
      f.slots[myIdx].length < (f.environment.includes("mud") ? 4 : 3)
    );
    const canPass = !hasTroops || !hasOpenFlags;

    return (
      <div style={S.card}>
        <div style={S.cardTitle}>🃏 Play a Card</div>
        {!selectedCard && (
          <p style={{ color: "#8b949e", fontSize: 13, margin: "4px 0" }}>
            Select a card from your hand, then click a flag on the board.
          </p>
        )}
        {selectedCard && !isGuile && (
          <p style={{ color: "#c9a84c", fontSize: 13, margin: "4px 0" }}>
            Click a highlighted flag to play {cardLabel(selectedCard)}.
          </p>
        )}
        {selectedCard && isGuile && (
          <div style={{ display: "flex", gap: 8, alignItems: "center", margin: "8px 0" }}>
            <span style={{ color: "#c9a84c", fontSize: 13 }}>Play {cardLabel(selectedCard)}?</span>
            <button onClick={() => {
              sendAction({ kind: getPlayActionKind(selectedCard), card_index: selectedHandIndex });
              setSelectedHandIndex(null);
            }} style={bs(true)}>Play</button>
            <button onClick={() => setSelectedHandIndex(null)} style={bs(false)}>Cancel</button>
          </div>
        )}
        <div style={{ display: "flex", gap: 8, marginTop: 8 }}>
          {selectedCard && !isGuile && (
            <button onClick={() => setSelectedHandIndex(null)} style={bs(false)}>Cancel</button>
          )}
          {canPass && (
            <button onClick={() => sendAction({ kind: "pass" })} style={bs(false)}>Pass</button>
          )}
        </div>
        {autoClaimToggle}
      </div>
    );
  }

  // ── Claim Flags Phase ──
  if (phase === "claim_flags") {
    return (
      <div style={S.card}>
        <div style={S.cardTitle}>🏴 Claim Flags</div>
        <p style={{ color: "#8b949e", fontSize: 13, margin: "4px 0" }}>
          Click highlighted flags to claim them, then finish.
        </p>
        <button onClick={() => sendAction({ kind: "done_claiming" })} style={{ ...bs(true), marginTop: 8 }}>
          Done Claiming
        </button>
        {autoClaimToggle}
      </div>
    );
  }

  // ── Draw Card Phase ──
  if (phase === "draw_card") {
    const troopCount = state.troop_deck;
    const tacticsCount = state.tactics_deck;
    return (
      <div style={S.card}>
        <div style={S.cardTitle}>📥 Draw a Card</div>
        <div style={{ display: "flex", gap: 8 }}>
          <button onClick={() => sendAction({ kind: "draw_card", deck: "troop" })}
            disabled={troopCount === 0} style={bs(true, troopCount === 0)}>
            Troop Deck ({troopCount})
          </button>
          <button onClick={() => sendAction({ kind: "draw_card", deck: "tactics" })}
            disabled={tacticsCount === 0} style={bs(false, tacticsCount === 0)}>
            Tactics Deck ({tacticsCount})
          </button>
        </div>
        {autoClaimToggle}
      </div>
    );
  }

  // ── Sub-phases ──
  if (phase === "sub_phase") {
    if (sub === "scout_draw") {
      const ss = state.scout_state || {};
      return (
        <div style={S.card}>
          <div style={S.cardTitle}>👁️ Scout — Draw Cards</div>
          <p style={{ color: "#c9a84c", fontSize: 13, margin: "4px 0" }}>
            Draws remaining: {ss.draws_remaining}
          </p>
          <div style={{ display: "flex", gap: 8 }}>
            <button onClick={() => sendAction({ kind: "scout_draw_card", deck: "troop" })}
              disabled={state.troop_deck === 0} style={bs(true, state.troop_deck === 0)}>
              Troop ({state.troop_deck})
            </button>
            <button onClick={() => sendAction({ kind: "scout_draw_card", deck: "tactics" })}
              disabled={state.tactics_deck === 0} style={bs(false, state.tactics_deck === 0)}>
              Tactics ({state.tactics_deck})
            </button>
          </div>
        </div>
      );
    }

    if (sub === "scout_return") {
      const ss = state.scout_state || {};
      return (
        <div style={S.card}>
          <div style={S.cardTitle}>👁️ Scout — Return Cards</div>
          <p style={{ color: "#c9a84c", fontSize: 13, margin: "4px 0" }}>
            Click a card in your hand to return it. Returns remaining: {ss.returns_remaining}
          </p>
        </div>
      );
    }

    if (sub === "redeploy_pick") {
      return (
        <div style={S.card}>
          <div style={S.cardTitle}>🔄 Redeploy — Pick a Card</div>
          <p style={{ color: "#c9a84c", fontSize: 13, margin: "4px 0" }}>
            Click one of your cards on the board to pick up.
          </p>
        </div>
      );
    }

    if (sub === "redeploy_place") {
      const rs = state.redeploy_state || {};
      return (
        <div style={S.card}>
          <div style={S.cardTitle}>🔄 Redeploy — Place Card</div>
          <div style={{ display: "flex", alignItems: "center", gap: 12, margin: "8px 0" }}>
            <span style={{ color: "#8b949e", fontSize: 13 }}>Moving:</span>
            {rs.picked_card && <CardDisplay card={rs.picked_card} small />}
          </div>
          <p style={{ color: "#c9a84c", fontSize: 13, margin: "4px 0" }}>
            Click a highlighted flag, or discard.
          </p>
          <button onClick={() => sendAction({ kind: "redeploy_discard" })} style={bs(false)}>
            Discard Card
          </button>
        </div>
      );
    }

    if (sub === "deserter_pick") {
      return (
        <div style={S.card}>
          <div style={S.cardTitle}>🏃 Deserter — Pick Enemy Card</div>
          <p style={{ color: "#c9a84c", fontSize: 13, margin: "4px 0" }}>
            Click an opponent's card on the board to remove it.
          </p>
        </div>
      );
    }

    if (sub === "traitor_pick") {
      return (
        <div style={S.card}>
          <div style={S.cardTitle}>🗡️ Traitor — Steal a Troop</div>
          <p style={{ color: "#c9a84c", fontSize: 13, margin: "4px 0" }}>
            Click an opponent's troop card on the board.
          </p>
        </div>
      );
    }

    if (sub === "traitor_place") {
      const ts = state.traitor_state || {};
      return (
        <div style={S.card}>
          <div style={S.cardTitle}>🗡️ Traitor — Place Stolen Card</div>
          <div style={{ display: "flex", alignItems: "center", gap: 12, margin: "8px 0" }}>
            <span style={{ color: "#8b949e", fontSize: 13 }}>Stolen:</span>
            {ts.picked_card && <CardDisplay card={ts.picked_card} small />}
          </div>
          <p style={{ color: "#c9a84c", fontSize: 13, margin: "4px 0" }}>
            Click a highlighted flag to place it.
          </p>
        </div>
      );
    }
  }

  return null;
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
          <h1 style={S.title}>⚔ BATTLE LINE ⚔</h1>
          <p style={{ fontSize: 14, color: "#8b949e", marginTop: 8, fontStyle: "italic" }}>
            A game of ancient warfare for 2 players
          </p>
        </div>

        {conn.error && (
          <div style={{ ...S.card, border: "1px solid #e74c3c44", padding: 12, marginBottom: 16 }}>
            <p style={{ color: "#e74c3c", fontSize: 13, margin: 0 }}>⚠ {conn.error}</p>
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
                <label style={{ color: "#c9a84c", fontSize: 13, display: "block", marginBottom: 6 }}>Your Name</label>
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
                <label style={{ color: "#c9a84c", fontSize: 13, display: "block", marginBottom: 6 }}>Room Code</label>
                <input value={joinCode} onChange={e => setJoinCode(e.target.value.toUpperCase())} placeholder="e.g. F6PYC"
                  maxLength={5} style={{ ...S.input, letterSpacing: 4, fontSize: 20, textAlign: "center", fontWeight: 700 }} />
              </div>
              <div style={{ marginBottom: 16, textAlign: "left" }}>
                <label style={{ color: "#c9a84c", fontSize: 13, display: "block", marginBottom: 6 }}>Your Name</label>
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
        <h1 style={{ ...S.title, fontSize: 28 }}>⚔ Game Lobby</h1>
      </div>

      {conn.error && (
        <div style={{ ...S.card, border: "1px solid #e74c3c44", padding: 12, marginBottom: 16 }}>
          <p style={{ color: "#e74c3c", fontSize: 13, margin: 0 }}>⚠ {conn.error}</p>
        </div>
      )}

      <div style={{ ...S.card, maxWidth: 440, margin: "0 auto", textAlign: "center" }}>
        <h2 style={S.cardTitle}>Room Code</h2>
        <div style={{
          fontSize: 48, fontWeight: 700, letterSpacing: 8, color: "#c9a84c",
          background: "rgba(0,0,0,0.3)", borderRadius: 12, padding: "16px 24px",
          marginBottom: 20, fontFamily: "monospace", userSelect: "all",
        }}>
          {conn.roomCode}
        </div>
        <p style={{ color: "#8b949e", fontSize: 13, marginBottom: 20 }}>
          Share this code with your opponent
        </p>

        <div style={{ marginBottom: 20 }}>
          <h3 style={{ color: "#c9a84c", fontSize: 16, marginBottom: 10 }}>Players ({conn.lobby.length}/2)</h3>
          {conn.lobby.map((p) => (
            <div key={p.player_id} style={{
              display: "flex", alignItems: "center", gap: 10,
              padding: "8px 16px", marginBottom: 4, borderRadius: 6,
              background: p.player_id === conn.playerId ? "rgba(201,168,76,0.08)" : "rgba(0,0,0,0.15)",
              border: p.player_id === conn.playerId ? "1px solid #c9a84c44" : "1px solid transparent",
            }}>
              <div style={{ width: 10, height: 10, borderRadius: "50%", background: p.connected ? "#27ae60" : "#e74c3c" }} />
              <span style={{ color: "#e8d5a3", fontSize: 14, flex: 1, textAlign: "left" }}>{p.name}</span>
              {p.player_id === conn.playerId && <span style={{ fontSize: 10, color: "#c9a84c" }}>you</span>}
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
  const oppIdx = 1 - myIdx;
  const me = state.players[myIdx];
  const hand = me.hand;
  const phase = state.phase;
  const subPhase = state.sub_phase;

  const [selectedHandIndex, setSelectedHandIndex] = useState(null);

  // Reset selection on phase/turn change
  useEffect(() => {
    setSelectedHandIndex(null);
  }, [phase, subPhase, state.turn_number]);

  const selectedCard = selectedHandIndex !== null ? hand[selectedHandIndex] : null;

  // ── Compute highlighted flags ──
  const highlightedFlags = useMemo(() => {
    if (!yourTurn) return new Set();

    // Play card phase: highlight valid target flags for selected card
    if (phase === "play_card" && selectedCard && needsFlagTarget(selectedCard)) {
      return new Set(getValidFlags(state, myIdx, selectedCard));
    }

    // Claim flags phase: highlight flags where our side is full
    if (phase === "claim_flags") {
      const valid = new Set();
      for (let fi = 0; fi < 9; fi++) {
        const flag = state.flags[fi];
        if (flag.claimed_by !== null) continue;
        const required = flag.environment.includes("mud") ? 4 : 3;
        if (flag.slots[myIdx].length >= required) valid.add(fi);
      }
      return valid;
    }

    // Redeploy place: highlight valid destination flags
    if (subPhase === "redeploy_place") {
      const fromFlag = state.redeploy_state?.from_flag;
      const valid = new Set();
      for (let fi = 0; fi < 9; fi++) {
        if (fi === fromFlag) continue;
        const flag = state.flags[fi];
        if (flag.claimed_by !== null) continue;
        const required = flag.environment.includes("mud") ? 4 : 3;
        if (flag.slots[myIdx].length < required) valid.add(fi);
      }
      return valid;
    }

    // Traitor place: highlight valid placement flags
    if (subPhase === "traitor_place") {
      const valid = new Set();
      for (let fi = 0; fi < 9; fi++) {
        const flag = state.flags[fi];
        if (flag.claimed_by !== null) continue;
        const required = flag.environment.includes("mud") ? 4 : 3;
        if (flag.slots[myIdx].length < required) valid.add(fi);
      }
      return valid;
    }

    return new Set();
  }, [phase, subPhase, selectedCard, yourTurn, state, myIdx]);

  // ── Is a board card clickable? ──
  const isCardClickable = useCallback((fi, ci, side) => {
    if (!yourTurn) return false;
    const flag = state.flags[fi];
    if (flag.claimed_by !== null) return false;
    const card = flag.slots[side]?.[ci];
    if (!card) return false;

    if (subPhase === "redeploy_pick" && side === myIdx) {
      return card.type === "troop" || card.subtype === "leader" || card.subtype === "morale";
    }
    if (subPhase === "deserter_pick" && side === oppIdx) {
      return card.type === "troop" || card.subtype === "leader" || card.subtype === "morale";
    }
    if (subPhase === "traitor_pick" && side === oppIdx) {
      return card.type === "troop";
    }
    return false;
  }, [subPhase, myIdx, oppIdx, yourTurn, state]);

  // ── Handle flag click ──
  const handleFlagClick = useCallback((fi) => {
    if (!yourTurn) return;

    if (phase === "play_card" && selectedCard && needsFlagTarget(selectedCard)) {
      const kind = getPlayActionKind(selectedCard);
      sendAction({ kind, card_index: selectedHandIndex, flag_index: fi });
      setSelectedHandIndex(null);
      return;
    }
    if (phase === "claim_flags") {
      sendAction({ kind: "claim_flag", flag_index: fi });
      return;
    }
    if (subPhase === "redeploy_place") {
      sendAction({ kind: "redeploy_place_to_flag", flag_index: fi });
      return;
    }
    if (subPhase === "traitor_place") {
      sendAction({ kind: "traitor_place", flag_index: fi });
      return;
    }
  }, [phase, subPhase, selectedCard, selectedHandIndex, yourTurn, sendAction]);

  // ── Handle board card click ──
  const handleBoardCardClick = useCallback((fi, ci, side) => {
    if (!yourTurn) return;

    if (subPhase === "redeploy_pick" && side === myIdx) {
      sendAction({ kind: "redeploy_pick", flag_index: fi, card_index_at_flag: ci });
      return;
    }
    if (subPhase === "deserter_pick" && side === oppIdx) {
      sendAction({ kind: "deserter_pick", flag_index: fi, card_index_at_flag: ci });
      return;
    }
    if (subPhase === "traitor_pick" && side === oppIdx) {
      sendAction({ kind: "traitor_pick", flag_index: fi, card_index_at_flag: ci });
      return;
    }
  }, [subPhase, myIdx, oppIdx, yourTurn, sendAction]);

  // ── Handle hand card click ──
  const handleHandCardClick = useCallback((index) => {
    if (!yourTurn) return;

    // Scout return: clicking a hand card returns it
    if (subPhase === "scout_return") {
      const card = hand[index];
      const deck = card.type === "troop" ? "troop" : "tactics";
      sendAction({ kind: "scout_return_card", card_index: index, deck });
      return;
    }

    // Play card phase: toggle selection
    if (phase === "play_card") {
      setSelectedHandIndex(selectedHandIndex === index ? null : index);
    }
  }, [phase, subPhase, yourTurn, selectedHandIndex, hand, sendAction]);

  // Hand is interactive during play_card and scout_return
  const handDisabled = !yourTurn || (phase !== "play_card" && subPhase !== "scout_return");

  return (
    <div style={S.content}>
      {/* Title bar */}
      <div style={{
        display: "flex", alignItems: "center", justifyContent: "space-between",
        borderBottom: "2px solid #30363d", paddingBottom: 10, marginBottom: 12,
      }}>
        <h1 style={{ ...S.title, fontSize: 24 }}>⚔ BATTLE LINE</h1>
        <div style={{ display: "flex", alignItems: "center", gap: 14 }}>
          <span style={{ color: "#c9a84c", fontSize: 14, fontWeight: 700 }}>Turn {state.turn_number + 1}</span>
          <span style={{
            fontSize: 12, padding: "3px 10px", borderRadius: 6, fontWeight: 600,
            background: yourTurn ? "rgba(201,168,76,0.15)" : "rgba(139,148,158,0.1)",
            color: yourTurn ? "#c9a84c" : "#8b949e",
            border: yourTurn ? "1px solid #c9a84c44" : "1px solid #30363d44",
          }}>
            {yourTurn ? "Your Turn" : "Opponent's Turn"}
          </span>
          <div style={{
            fontSize: 10, padding: "3px 8px", borderRadius: 4,
            background: conn.connected ? "rgba(39,174,96,0.15)" : "rgba(231,76,60,0.15)",
            color: conn.connected ? "#27ae60" : "#e74c3c",
            border: conn.connected ? "1px solid #27ae6044" : "1px solid #e74c3c44",
          }}>{conn.connected ? "● Online" : "○ Reconnecting..."}</div>
          <FormationReference />
        </div>
      </div>

      {/* Error banner */}
      {conn.error && (
        <div style={{ padding: "8px 16px", marginBottom: 10, borderRadius: 6, background: "rgba(231,76,60,0.12)", border: "1px solid #e74c3c44" }}>
          <span style={{ fontSize: 13, color: "#e74c3c" }}>⚠ {conn.error}</span>
        </div>
      )}

      {/* Phase indicator */}
      <PhaseIndicator phase={phase} subPhase={subPhase} />

      {/* Action panel */}
      <ActionPanel state={state} myIdx={myIdx} yourTurn={yourTurn} sendAction={sendAction}
        selectedHandIndex={selectedHandIndex} setSelectedHandIndex={setSelectedHandIndex} />

      {/* Board */}
      <BoardDisplay state={state} myIdx={myIdx}
        onFlagClick={handleFlagClick} onCardClick={handleBoardCardClick}
        highlightedFlags={highlightedFlags} isCardClickable={isCardClickable} />

      {/* Hand */}
      <HandDisplay hand={hand} onCardClick={handleHandCardClick}
        selectedIndex={selectedHandIndex} disabled={handDisabled} />

      {/* Deck area + Game log */}
      <div style={{ display: "flex", gap: 16, alignItems: "flex-start", flexWrap: "wrap" }}>
        <DeckArea troopCount={state.troop_deck} tacticsCount={state.tactics_deck} discards={state.discard} />
        <div style={{ flex: 1, minWidth: 200 }}>
          <GameLog logs={conn.gameLogs} />
        </div>
      </div>
    </div>
  );
}

// ─── GAME OVER SCREEN ──────────────────────────────────────────────

function GameOverScreen({ conn }) {
  const state = conn.gameState;
  const myIdx = getMyPlayerIdx(state);
  const winnerIdx = state.winner;
  const iWon = winnerIdx === myIdx;
  const winnerName = state.players[winnerIdx]?.name || "Unknown";

  // Count claimed flags per player
  const flagCounts = [0, 0];
  state.flags.forEach(f => {
    if (f.claimed_by !== null) flagCounts[f.claimed_by]++;
  });

  // Check win type
  let winType = "Envelopment";
  for (let i = 0; i <= 6; i++) {
    if (state.flags[i].claimed_by === winnerIdx &&
        state.flags[i + 1].claimed_by === winnerIdx &&
        state.flags[i + 2].claimed_by === winnerIdx) {
      winType = "Breakthrough";
      break;
    }
  }

  return (
    <div style={S.content}>
      <div style={{ textAlign: "center", padding: "40px 0 24px", borderBottom: "2px solid #30363d", marginBottom: 30 }}>
        <h1 style={S.title}>⚔ Game Over ⚔</h1>
      </div>

      {/* Winner banner */}
      <div style={{
        ...S.card, textAlign: "center", padding: 30,
        border: iWon ? "2px solid #27ae60" : "2px solid #e74c3c",
        boxShadow: iWon ? "0 0 30px rgba(39,174,96,0.2)" : "0 0 30px rgba(231,76,60,0.2)",
      }}>
        <div style={{ fontSize: 48, marginBottom: 12 }}>{iWon ? "🏆" : "⚔"}</div>
        <h2 style={{ fontSize: 28, color: iWon ? "#27ae60" : "#e74c3c", margin: "0 0 8px" }}>
          {iWon ? "Victory!" : "Defeat"}
        </h2>
        <p style={{ fontSize: 16, color: "#e8d5a3", margin: "0 0 8px" }}>
          {winnerName} wins by {winType}!
        </p>
        <p style={{ fontSize: 14, color: "#8b949e", margin: 0 }}>
          Flags: {state.players[0].name} {flagCounts[0]} — {flagCounts[1]} {state.players[1].name}
        </p>
      </div>

      {/* Final board */}
      <BoardDisplay state={state} myIdx={myIdx}
        onFlagClick={() => {}} onCardClick={() => {}}
        highlightedFlags={new Set()} isCardClickable={() => false} />

      {/* Flag summary */}
      <div style={{ ...S.card, padding: 16 }}>
        <div style={S.cardTitle}>Flag Summary</div>
        <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
          {state.flags.map((flag, fi) => {
            const claimedByMe = flag.claimed_by === myIdx;
            const claimedByOpp = flag.claimed_by === (1 - myIdx);
            return (
              <div key={fi} style={{
                width: 60, padding: "8px 4px", borderRadius: 6, textAlign: "center",
                background: claimedByMe ? "rgba(39,174,96,0.12)" : claimedByOpp ? "rgba(231,76,60,0.12)" : "rgba(0,0,0,0.2)",
                border: claimedByMe ? "1px solid #27ae6044" : claimedByOpp ? "1px solid #e74c3c44" : "1px solid #30363d44",
              }}>
                <div style={{ fontSize: 16, fontWeight: 700, color: claimedByMe ? "#27ae60" : claimedByOpp ? "#e74c3c" : "#8b949e" }}>
                  {fi + 1}
                </div>
                <div style={{ fontSize: 10, color: "#6a604a" }}>
                  {claimedByMe ? "You" : claimedByOpp ? "Opp" : "—"}
                </div>
              </div>
            );
          })}
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
  if (conn.gameOver || state.winner !== null) {
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
