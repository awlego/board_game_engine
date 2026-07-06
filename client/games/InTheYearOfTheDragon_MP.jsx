import { useState, useEffect, useRef, useCallback, useMemo } from "react";

// ─── CONFIGURATION ─────────────────────────────────────────────────
import { WS_URL } from "../ws.js";

// ─── PERSON TYPE CONFIGURATION (mirrors server) ────────────────────
const PERSON_TYPES = {
  monk:         { name: "Monk",          color: "#8B4513", icon: "☸️", resourceIcon: "☸️" },
  healer:       { name: "Healer",        color: "#3498db", icon: "⚕️", resourceIcon: "⚗️" },
  pyrotechnist: { name: "Pyrotechnist",  color: "#8e44ad", icon: "🎆", resourceIcon: "🚀" },
  craftsman:    { name: "Craftsman",     color: "#d4a574", icon: "🔨", resourceIcon: "🔨" },
  courtLady:    { name: "Court Lady",    color: "#d4a017", icon: "🪭", resourceIcon: "🐉" },
  taxCollector: { name: "Tax Collector", color: "#f1c40f", icon: "💰", resourceIcon: "🪙" },
  warrior:      { name: "Warrior",       color: "#c0392b", icon: "⚔️", resourceIcon: "🪖" },
  scholar:      { name: "Scholar",       color: "#ecf0f1", icon: "📜", resourceIcon: "📖" },
  farmer:       { name: "Farmer",        color: "#27ae60", icon: "🌾", resourceIcon: "🌾" },
};

const ACTION_INFO = {
  taxes:     { name: "Taxes",             icon: "💰", desc: "Collect 2 yuan, +1 per tax collector coin symbol",     bonus: "taxCollector", unit: "¥" },
  build:     { name: "Build",             icon: "🏗️", desc: "Gain 1 palace floor, +1 per craftsman hammer symbol",  bonus: "craftsman",    unit: "floors" },
  harvest:   { name: "Harvest",           icon: "🌾", desc: "Gain 1 rice tile, +1 per farmer rice symbol",          bonus: "farmer",       unit: "rice" },
  fireworks: { name: "Fireworks Display", icon: "🎆", desc: "Gain 1 firework, +1 per pyrotechnist rocket symbol",  bonus: "pyrotechnist", unit: "fireworks" },
  military:  { name: "Military Parade",   icon: "⚔️", desc: "Advance 1 on person track, +1 per warrior helmet",    bonus: "warrior",      unit: "steps" },
  research:  { name: "Research",          icon: "📜", desc: "Score 1 VP, +1 per scholar book symbol",               bonus: "scholar",      unit: "VP" },
  privilege: { name: "Privilege",         icon: "🏅", desc: "Buy a privilege: small (2¥, 1🐉) or large (7¥, 2🐉)", bonus: null,           unit: "" },
};

const PHASES = [
  { id: "action",  name: "Action",  icon: "⚡" },
  { id: "person",  name: "Person",  icon: "👤" },
  { id: "event",   name: "Event",   icon: "📜" },
  { id: "scoring", name: "Scoring", icon: "⭐" },
];

// ─── STYLES ────────────────────────────────────────────────────────

const font = `'Palatino Linotype', 'Book Antiqua', Palatino, Georgia, serif`;
const S = {
  app: {
    fontFamily: font, minHeight: "100vh",
    background: "linear-gradient(160deg, #1a0a00 0%, #2d1810 30%, #1a0a00 100%)",
    color: "#f0e6d3", position: "relative", overflow: "hidden",
  },
  overlay: {
    position: "fixed", inset: 0, pointerEvents: "none", zIndex: 0,
    backgroundImage: `repeating-linear-gradient(45deg, transparent, transparent 35px, rgba(139,69,19,0.03) 35px, rgba(139,69,19,0.03) 70px)`,
  },
  content: { position: "relative", zIndex: 1, maxWidth: 1200, margin: "0 auto", padding: "20px" },
  card: {
    background: "linear-gradient(135deg, rgba(45,24,16,0.9) 0%, rgba(30,15,8,0.95) 100%)",
    border: "1px solid #5a3a20", borderRadius: 8, padding: 24, marginBottom: 20,
    boxShadow: "0 4px 20px rgba(0,0,0,0.4)",
  },
  cardTitle: {
    fontFamily: font, fontSize: 22, color: "#d4a017",
    marginBottom: 16, borderBottom: "1px solid #5a3a20", paddingBottom: 8,
  },
  btn: {
    fontFamily: font, fontSize: 15, padding: "10px 24px", borderRadius: 6,
    border: "1px solid #8B4513",
    background: "linear-gradient(135deg, #5a3a20 0%, #3d2510 100%)",
    color: "#f0e6d3", cursor: "pointer", transition: "all 0.2s", fontWeight: 600,
  },
  btnP: { background: "linear-gradient(135deg, #8B4513 0%, #5a3a20 100%)", border: "1px solid #d4a017" },
  dis: { opacity: 0.4, cursor: "not-allowed" },
  title: {
    fontFamily: font, fontSize: 42, fontWeight: 700, color: "#d4a017",
    textShadow: "0 2px 8px rgba(0,0,0,0.5)", margin: 0, letterSpacing: 2,
  },
  input: {
    flex: 1, fontFamily: font, fontSize: 14, padding: "8px 12px",
    borderRadius: 6, border: "1px solid #5a3a20",
    background: "rgba(0,0,0,0.3)", color: "#f0e6d3", outline: "none",
  },
};

function bs(primary, disabled) {
  return { ...S.btn, ...(primary ? S.btnP : {}), ...(disabled ? S.dis : {}) };
}

// ─── UTILITIES ─────────────────────────────────────────────────────

function countSymbols(player, typeId) {
  let total = 0;
  (player.palaces || []).forEach(pal =>
    (pal.persons || []).forEach(per => {
      if (per.type_id === typeId) total += per.symbols;
    })
  );
  return total;
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
      // If we have a token, auto-reconnect
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
          // Auto-auth after create
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
          // Could add chat UI later
          break;
      }
    };

    ws.onclose = () => {
      setConnected(false);
      // Auto-reconnect after 2s
      setTimeout(() => {
        if (tokenRef.current) connect();
      }, 2000);
    };

    ws.onerror = () => setError("Connection error");
  }, []);


  const createRoom = useCallback((name) => {
    connect(() => {
      // Small delay to ensure ws is ready
      setTimeout(() => {
        wsRef.current?.send(JSON.stringify({ type: "create", game: "dragon", name }));
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

// ─── SHARED VISUAL COMPONENTS ──────────────────────────────────────

function ResourceBadge({ icon, label, value, highlight }) {
  return (
    <div style={{
      display: "inline-flex", alignItems: "center", gap: 6,
      background: highlight ? "rgba(212,160,23,0.12)" : "rgba(0,0,0,0.2)",
      border: highlight ? "1px solid #d4a01744" : "1px solid #5a3a2044",
      borderRadius: 6, padding: "4px 10px",
    }}>
      <span style={{ fontSize: 14 }}>{icon}</span>
      <div style={{ display: "flex", flexDirection: "column" }}>
        <span style={{ fontSize: 9, color: "#806040", lineHeight: 1, textTransform: "uppercase", letterSpacing: 0.5 }}>{label}</span>
        <span style={{ fontSize: 15, fontWeight: 700, color: highlight ? "#d4a017" : "#c0a070", lineHeight: 1.2 }}>{value}</span>
      </div>
    </div>
  );
}

function PalaceDisplay({ palace, palaceIndex }) {
  const emptySlots = palace.floors - (palace.persons || []).length;
  return (
    <div style={{
      background: "rgba(0,0,0,0.2)", border: "1px solid #5a3a2066",
      borderRadius: 8, padding: 10, minWidth: 110,
    }}>
      <div style={{
        display: "flex", alignItems: "center", justifyContent: "space-between",
        marginBottom: 6, paddingBottom: 4, borderBottom: "1px solid #5a3a2044",
      }}>
        <span style={{ fontSize: 10, color: "#a08060", fontWeight: 600 }}>Palace {palaceIndex + 1}</span>
        <div style={{ display: "flex", gap: 2 }}>
          {Array.from({ length: palace.floors }).map((_, i) => (
            <div key={i} style={{
              width: 8, height: 10,
              background: i < (palace.persons || []).length ? "#d4a017" : "#5a3a2088",
              borderRadius: 1, border: "1px solid #8B451366",
            }} />
          ))}
        </div>
      </div>
      <div style={{ display: "flex", flexDirection: "column", gap: 4 }}>
        {(palace.persons || []).map((person, i) => {
          const t = PERSON_TYPES[person.type_id];
          if (!t) return null;
          return (
            <div key={i} style={{
              display: "flex", alignItems: "center", gap: 6,
              background: `${t.color}15`, border: `1px solid ${t.color}44`,
              borderRadius: 5, padding: "3px 8px",
            }}>
              <span style={{ fontSize: 14 }}>{t.icon}</span>
              <div style={{ flex: 1, minWidth: 0 }}>
                <div style={{ fontSize: 10, color: t.color, fontWeight: 600, lineHeight: 1.2 }}>{t.name}</div>
                <div style={{ display: "flex", gap: 2, alignItems: "center" }}>
                  {Array.from({ length: person.symbols }).map((_, j) => (
                    <span key={j} style={{ fontSize: 9 }}>{t.resourceIcon}</span>
                  ))}
                  <span style={{ fontSize: 8, color: "#80604088", marginLeft: 2 }}>
                    {person.experience === "old" ? "exp" : ""}
                  </span>
                </div>
              </div>
            </div>
          );
        })}
        {Array.from({ length: Math.max(0, emptySlots) }).map((_, i) => (
          <div key={`empty-${i}`} style={{
            display: "flex", alignItems: "center", justifyContent: "center",
            border: "1px dashed #5a3a2044", borderRadius: 5, padding: "3px 8px", height: 28,
          }}>
            <span style={{ fontSize: 9, color: "#5a3a2066" }}>empty</span>
          </div>
        ))}
      </div>
    </div>
  );
}

function PersonTileDisplay({ tile, onClick, selected, small, disabled }) {
  const t = PERSON_TYPES[tile.type_id];
  if (!t) return null;
  return (
    <div onClick={disabled ? undefined : onClick} style={{
      width: small ? 72 : 90, minHeight: small ? 82 : 100,
      background: selected ? `linear-gradient(135deg, ${t.color}44, ${t.color}22)`
        : "linear-gradient(135deg, rgba(50,30,20,0.9), rgba(35,20,12,0.95))",
      border: selected ? `2px solid ${t.color}` : "1px solid #5a3a2088",
      borderRadius: 6, padding: small ? 6 : 8,
      cursor: disabled ? "default" : "pointer",
      display: "flex", flexDirection: "column", alignItems: "center", justifyContent: "center", gap: 2,
      transition: "all 0.15s", opacity: disabled ? 0.35 : 1,
      boxShadow: selected ? `0 0 12px ${t.color}44` : "0 2px 6px rgba(0,0,0,0.3)",
    }}>
      <span style={{ fontSize: small ? 20 : 26 }}>{t.icon}</span>
      <span style={{ fontSize: small ? 9 : 11, color: t.color, fontWeight: 600, textAlign: "center" }}>{t.name}</span>
      <span style={{ fontSize: small ? 8 : 10, color: "#a08060" }}>
        {tile.experience === "old" ? "Experienced" : "Young"}
      </span>
      <div style={{ display: "flex", gap: 2, marginTop: 2 }}>
        {Array.from({ length: tile.symbols }).map((_, i) => (
          <span key={i} style={{ fontSize: small ? 10 : 12 }}>{t.resourceIcon}</span>
        ))}
      </div>
      <span style={{
        fontSize: small ? 10 : 12, fontWeight: 700, color: "#d4a017", marginTop: 2,
        background: "rgba(0,0,0,0.3)", borderRadius: 4, padding: "1px 6px",
      }}>+{tile.value}</span>
    </div>
  );
}

function PlayerArea({ player, isActive, isYou }) {
  const totalDragons = (player.privileges?.small || 0) + (player.privileges?.large || 0) * 2;
  return (
    <div style={{
      ...S.card, padding: 14,
      border: isActive ? `2px solid ${player.color.primary}` : isYou ? `1px solid ${player.color.primary}66` : S.card.border,
      boxShadow: isActive ? `0 0 20px ${player.color.primary}44, 0 4px 20px rgba(0,0,0,0.4)` : S.card.boxShadow,
    }}>
      <div style={{ display: "flex", alignItems: "center", gap: 10, marginBottom: 10 }}>
        <div style={{
          width: 18, height: 18, borderRadius: "50%",
          background: player.color.primary, boxShadow: `0 0 6px ${player.color.primary}88`,
        }} />
        <span style={{ fontFamily: font, fontSize: 17, color: player.color.light, fontWeight: 700, flex: 1 }}>
          {player.name} {isYou ? "(you)" : ""}
        </span>
        {isActive && (
          <span style={{
            fontSize: 10, color: "#1a0a00", background: player.color.primary,
            padding: "2px 10px", borderRadius: 10, fontWeight: 700,
          }}>⬤ TURN</span>
        )}
      </div>
      <div style={{ display: "flex", gap: 6, marginBottom: 10, flexWrap: "wrap" }}>
        <ResourceBadge icon="💰" label="Yuan" value={player.yuan} />
        <ResourceBadge icon="🌾" label="Rice" value={player.rice} />
        <ResourceBadge icon="🎆" label="Fireworks" value={player.fireworks} />
        {(player.privileges?.small > 0 || player.privileges?.large > 0) && (
          <div style={{
            display: "inline-flex", alignItems: "center", gap: 6,
            background: "rgba(0,0,0,0.2)", border: "1px solid #5a3a2044",
            borderRadius: 6, padding: "4px 10px",
          }}>
            <span style={{ fontSize: 14 }}>🐉</span>
            <div style={{ display: "flex", flexDirection: "column" }}>
              <span style={{ fontSize: 9, color: "#806040", lineHeight: 1, textTransform: "uppercase", letterSpacing: 0.5 }}>Privileges</span>
              <div style={{ display: "flex", gap: 4, alignItems: "center" }}>
                {player.privileges?.small > 0 && <span style={{ fontSize: 12, color: "#c0a070" }}>{player.privileges.small}×🐉</span>}
                {player.privileges?.large > 0 && <span style={{ fontSize: 12, color: "#d4a017" }}>{player.privileges.large}×🐉🐉</span>}
                <span style={{ fontSize: 10, color: "#806040" }}>= {totalDragons}/round</span>
              </div>
            </div>
          </div>
        )}
        <ResourceBadge icon="👤" label="Person Track" value={player.person_track} highlight />
        <ResourceBadge icon="⭐" label="Score" value={player.scoring_track} highlight />
      </div>
      <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
        {(player.palaces || []).map((palace, i) => (
          <PalaceDisplay key={i} palace={palace} palaceIndex={i} />
        ))}
      </div>
    </div>
  );
}

function PlayerAreasGrid({ players, activePlayerIdx, yourPlayerIdx }) {
  return (
    <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(300px, 1fr))", gap: 12, marginTop: 16 }}>
      {players.map((p, i) => (
        <PlayerArea key={i} player={p} isActive={i === activePlayerIdx} isYou={i === yourPlayerIdx} />
      ))}
    </div>
  );
}

function PhaseTracker({ currentPhase }) {
  const curIdx = PHASES.findIndex(p => p.id === currentPhase);
  return (
    <div style={{ display: "flex", gap: 3, alignItems: "center" }}>
      {PHASES.map((phase, i) => {
        const isPast = i < curIdx, isCurrent = i === curIdx;
        return (
          <div key={phase.id} style={{ display: "flex", alignItems: "center", gap: 3 }}>
            <div style={{
              padding: "4px 12px", borderRadius: 6,
              background: isCurrent ? "rgba(212,160,23,0.2)" : isPast ? "rgba(30,15,8,0.5)" : "rgba(45,24,16,0.6)",
              border: isCurrent ? "2px solid #d4a017" : "1px solid #5a3a2066",
              opacity: isPast ? 0.45 : 1,
              display: "flex", alignItems: "center", gap: 5,
            }}>
              <span style={{ fontSize: 13 }}>{phase.icon}</span>
              <span style={{ fontSize: 11, fontWeight: isCurrent ? 700 : 400, color: isCurrent ? "#d4a017" : "#a08060" }}>
                {phase.name}
              </span>
            </div>
            {i < PHASES.length - 1 && <span style={{ color: isPast ? "#5a3a2044" : "#5a3a2088", fontSize: 10 }}>›</span>}
          </div>
        );
      })}
    </div>
  );
}

function TurnOrderBar({ players, turnOrder, currentOrderIdx, phaseName }) {
  if (!turnOrder || turnOrder.length === 0) return null;
  return (
    <div style={{
      display: "flex", alignItems: "center", gap: 8, flexWrap: "wrap",
      padding: "8px 12px", marginBottom: 10, background: "rgba(0,0,0,0.15)", borderRadius: 6,
    }}>
      <span style={{ fontSize: 11, color: "#806040", fontWeight: 600, whiteSpace: "nowrap" }}>{phaseName} order:</span>
      {turnOrder.map((pIdx, i) => {
        const p = players[pIdx];
        if (!p) return null;
        const isCurrent = i === currentOrderIdx, isDone = i < currentOrderIdx;
        return (
          <div key={i} style={{ display: "flex", alignItems: "center", gap: 3 }}>
            {i > 0 && <span style={{ color: "#5a3a2066", fontSize: 10 }}>→</span>}
            <div style={{
              display: "flex", alignItems: "center", gap: 5,
              padding: "3px 10px", borderRadius: 10,
              background: isCurrent ? `${p.color.primary}33` : "transparent",
              border: isCurrent ? `2px solid ${p.color.primary}` : "1px solid transparent",
              opacity: isDone ? 0.4 : 1,
            }}>
              <div style={{
                width: 10, height: 10, borderRadius: "50%", background: p.color.primary,
                boxShadow: isCurrent ? `0 0 6px ${p.color.primary}` : "none",
              }} />
              <span style={{
                fontSize: 11, color: isCurrent ? p.color.light : "#a08060",
                fontWeight: isCurrent ? 700 : 400, textDecoration: isDone ? "line-through" : "none",
              }}>{p.name}</span>
              {isCurrent && <span style={{ fontSize: 9, color: p.color.primary }}>◄</span>}
            </div>
          </div>
        );
      })}
    </div>
  );
}

function EventTrack({ events, currentRound }) {
  return (
    <div style={{ display: "flex", gap: 4, flexWrap: "wrap", padding: "8px 0" }}>
      {(events || []).map((event, i) => (
        <div key={i} style={{
          width: 72, height: 52,
          background: i < currentRound ? "rgba(30,15,8,0.6)"
            : i === currentRound ? `linear-gradient(135deg, ${event.color}33, ${event.color}11)`
            : "rgba(45,24,16,0.7)",
          border: i === currentRound ? `2px solid ${event.color}` : "1px solid #5a3a2066",
          borderRadius: 6, display: "flex", flexDirection: "column",
          alignItems: "center", justifyContent: "center", gap: 2,
          opacity: i < currentRound ? 0.4 : 1,
        }}>
          <span style={{ fontSize: 16 }}>{event.icon}</span>
          <span style={{ fontSize: 8, color: "#a08060", textAlign: "center" }}>{event.name}</span>
          <span style={{ fontSize: 8, color: "#705030" }}>Month {i + 1}</span>
        </div>
      ))}
    </div>
  );
}

function ActionCard({ actionId, selected, onClick, disabled, player }) {
  const info = ACTION_INFO[actionId];
  if (!info) return null;
  const bonus = info.bonus ? countSymbols(player, info.bonus) : 0;
  const previewTotal = info.bonus ? (actionId === "taxes" ? 2 + bonus : 1 + bonus) : null;

  return (
    <div onClick={disabled ? undefined : onClick} style={{
      width: 130, padding: 10, borderRadius: 8, cursor: disabled ? "default" : "pointer",
      background: selected
        ? "linear-gradient(135deg, rgba(212,160,23,0.15), rgba(139,69,19,0.2))"
        : "linear-gradient(135deg, rgba(40,22,12,0.95), rgba(25,12,6,0.95))",
      border: selected ? "2px solid #d4a017" : "1px solid #5a3a2088",
      boxShadow: selected ? "0 0 12px rgba(212,160,23,0.2)" : "0 2px 6px rgba(0,0,0,0.3)",
      transition: "all 0.15s", opacity: disabled ? 0.5 : 1,
      display: "flex", flexDirection: "column", gap: 4,
    }}>
      <div style={{ display: "flex", alignItems: "center", gap: 6 }}>
        <span style={{ fontSize: 20 }}>{info.icon}</span>
        <span style={{ fontSize: 12, fontWeight: 700, color: selected ? "#d4a017" : "#c0a070" }}>{info.name}</span>
      </div>
      <div style={{ fontSize: 9, color: "#907050", lineHeight: 1.3 }}>{info.desc}</div>
      {previewTotal !== null && (
        <div style={{
          fontSize: 10, color: "#d4a017", background: "rgba(0,0,0,0.25)",
          borderRadius: 4, padding: "2px 6px", marginTop: 2,
        }}>→ {previewTotal} {info.unit}</div>
      )}
    </div>
  );
}

function WaitingBanner({ phaseInfo, players, waitingFor }) {
  const waitNames = waitingFor.map(wid => {
    const p = players.find(pl => pl.player_id === wid);
    return p?.name || "???";
  });
  return (
    <div style={{
      ...S.card, textAlign: "center", padding: 20,
      border: "1px solid #d4a01744", background: "rgba(212,160,23,0.05)",
    }}>
      <div style={{ fontSize: 28, marginBottom: 8 }}>⏳</div>
      <p style={{ color: "#d4a017", fontSize: 16, fontWeight: 600, marginBottom: 4 }}>
        Waiting for {waitNames.join(", ")}
      </p>
      <p style={{ color: "#a08060", fontSize: 13 }}>{phaseInfo?.description}</p>
      <div style={{ display: "flex", justifyContent: "center", gap: 6, marginTop: 12 }}>
        {[0,1,2].map(i => (
          <div key={i} style={{
            width: 8, height: 8, borderRadius: "50%", background: "#d4a017",
            animation: `pulse 1.4s ease-in-out ${i * 0.2}s infinite`,
            opacity: 0.3,
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
      background: "rgba(0,0,0,0.2)", border: "1px solid #5a3a2044", borderRadius: 6,
      padding: "8px 12px", marginBottom: 12, maxHeight: 120, overflowY: "auto",
    }}>
      {recent.map((msg, i) => (
        <p key={logs.length - recent.length + i} style={{ fontSize: 12, color: "#a08060", margin: "3px 0", lineHeight: 1.4 }}>• {msg}</p>
      ))}
      <div ref={endRef} />
    </div>
  );
}

// ─── LOBBY SCREEN ──────────────────────────────────────────────────

function LobbyScreen({ conn }) {
  const [mode, setMode] = useState(null); // null | "create" | "join"
  const [name, setName] = useState("");
  const [joinCode, setJoinCode] = useState("");

  if (!conn.roomCode) {
    return (
      <div style={S.content}>
        <div style={{ textAlign: "center", padding: "30px 0 20px", borderBottom: "2px solid #8B4513", marginBottom: 30 }}>
          <h1 style={S.title}>🐉 In the Year of the Dragon 🐉</h1>
          <p style={{ fontSize: 14, color: "#a08060", marginTop: 6, fontStyle: "italic" }}>
            Multiplayer — A game of foresight and survival in ancient China, 1000 A.D.
          </p>
        </div>

        {conn.error && (
          <div style={{ ...S.card, border: "1px solid #c0392b", padding: 12, marginBottom: 16 }}>
            <p style={{ color: "#e74c3c", fontSize: 13, margin: 0 }}>⚠ {conn.error}</p>
          </div>
        )}

        <div style={{ ...S.card, maxWidth: 480, margin: "40px auto", textAlign: "center" }}>
          {!mode && (
            <>
              <h2 style={S.cardTitle}>Play Online</h2>
              <p style={{ color: "#c0a070", marginBottom: 24, lineHeight: 1.6 }}>
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
                <label style={{ color: "#d4a017", fontSize: 13, display: "block", marginBottom: 6 }}>Your Name</label>
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
                <label style={{ color: "#d4a017", fontSize: 13, display: "block", marginBottom: 6 }}>Room Code</label>
                <input value={joinCode} onChange={e => setJoinCode(e.target.value.toUpperCase())} placeholder="e.g. F6PYC"
                  maxLength={5} style={{ ...S.input, letterSpacing: 4, fontSize: 20, textAlign: "center", fontWeight: 700 }} />
              </div>
              <div style={{ marginBottom: 16, textAlign: "left" }}>
                <label style={{ color: "#d4a017", fontSize: 13, display: "block", marginBottom: 6 }}>Your Name</label>
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
      <div style={{ textAlign: "center", padding: "30px 0 20px", borderBottom: "2px solid #8B4513", marginBottom: 30 }}>
        <h1 style={{ ...S.title, fontSize: 32 }}>🐉 Game Lobby</h1>
      </div>

      {conn.error && (
        <div style={{ ...S.card, border: "1px solid #c0392b", padding: 12, marginBottom: 16 }}>
          <p style={{ color: "#e74c3c", fontSize: 13, margin: 0 }}>⚠ {conn.error}</p>
        </div>
      )}

      <div style={{ ...S.card, maxWidth: 480, margin: "0 auto", textAlign: "center" }}>
        <h2 style={S.cardTitle}>Room Code</h2>
        <div style={{
          fontSize: 48, fontWeight: 700, letterSpacing: 8, color: "#d4a017",
          background: "rgba(0,0,0,0.3)", borderRadius: 12, padding: "16px 24px",
          marginBottom: 20, fontFamily: "monospace", userSelect: "all",
        }}>
          {conn.roomCode}
        </div>
        <p style={{ color: "#a08060", fontSize: 13, marginBottom: 20 }}>
          Share this code with other players so they can join
        </p>

        <div style={{ marginBottom: 20 }}>
          <h3 style={{ color: "#d4a017", fontSize: 16, marginBottom: 10 }}>Players ({conn.lobby.length}/5)</h3>
          {conn.lobby.map((p, i) => (
            <div key={p.player_id} style={{
              display: "flex", alignItems: "center", gap: 10,
              padding: "8px 16px", marginBottom: 4, borderRadius: 6,
              background: p.player_id === conn.playerId ? "rgba(212,160,23,0.1)" : "rgba(0,0,0,0.15)",
              border: p.player_id === conn.playerId ? "1px solid #d4a01744" : "1px solid transparent",
            }}>
              <div style={{ width: 10, height: 10, borderRadius: "50%", background: p.connected ? "#27ae60" : "#c0392b" }} />
              <span style={{ color: "#f0e6d3", fontSize: 14, flex: 1, textAlign: "left" }}>{p.name}</span>
              {p.player_id === conn.playerId && <span style={{ fontSize: 10, color: "#d4a017" }}>you</span>}
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
          <p style={{ color: "#a08060", fontSize: 13, fontStyle: "italic" }}>Waiting for host to start the game...</p>
        )}
      </div>
    </div>
  );
}

// ─── DRAFT PHASE ───────────────────────────────────────────────────

function DraftPhase({ state, sendAction, yourTurn }) {
  const [sels, setSels] = useState([]);
  const players = state.players;
  const myIdx = state.your_player_idx;
  const drafterIdx = state.draft?.current_drafter;
  const usedCombos = new Set(state.draft?.used_combos || []);

  // Get young tiles from remaining, grouped by type
  const young = (state.remaining_tiles || []).filter(t => t.experience === "young");
  const byType = {};
  young.forEach(t => { if (!byType[t.type_id]) byType[t.type_id] = []; byType[t.type_id].push(t); });

  const comboKey = (a, b) => [a, b].sort().join("+");

  const canSel = (tile) => {
    if (sels.length >= 2 || sels.find(s => s.id === tile.id)) return false;
    if (sels.length === 1 && sels[0].type_id === tile.type_id) return false;
    if (sels.length === 1 && usedCombos.has(comboKey(sels[0].type_id, tile.type_id))) return false;
    return true;
  };

  const toggle = (tile) => {
    if (sels.find(s => s.id === tile.id)) setSels(sels.filter(s => s.id !== tile.id));
    else if (canSel(tile)) setSels([...sels, tile]);
  };

  const confirm = () => {
    if (sels.length !== 2) return;
    sendAction({ kind: "draft_pick", picks: [sels[0].type_id, sels[1].type_id] });
    setSels([]);
  };

  const drafter = players[drafterIdx];

  return (
    <div style={S.content}>
      <div style={{ textAlign: "center", padding: "20px 0 16px", borderBottom: "2px solid #8B4513", marginBottom: 20 }}>
        <h1 style={{ ...S.title, fontSize: 32 }}>🐉 Initial Draft</h1>
      </div>
      <div style={{ ...S.card, padding: 12 }}>
        <div style={{ fontSize: 12, color: "#d4a017", marginBottom: 6 }}>Event Track — Plan ahead!</div>
        <EventTrack events={state.events} currentRound={-1} />
      </div>

      {yourTurn ? (
        <div style={S.card}>
          <div style={{ display: "flex", alignItems: "center", gap: 12, marginBottom: 16 }}>
            <div style={{ width: 20, height: 20, borderRadius: "50%", background: drafter?.color?.primary }} />
            <h2 style={{ ...S.cardTitle, margin: 0, border: "none", padding: 0 }}>
              Your turn — Pick 2 different young courtiers
            </h2>
          </div>

          {usedCombos.size > 0 && (
            <p style={{ fontSize: 12, color: "#a08060", marginBottom: 12 }}>
              Forbidden: {Array.from(usedCombos).map(c => {
                const [a, b] = c.split("+");
                return `${PERSON_TYPES[a]?.name || a} + ${PERSON_TYPES[b]?.name || b}`;
              }).join(", ")}
            </p>
          )}

          <div style={{ display: "flex", gap: 8, flexWrap: "wrap", marginBottom: 20 }}>
            {Object.entries(byType).map(([tid, tiles]) => (
              <div key={tid} style={{ display: "flex", flexDirection: "column", alignItems: "center", gap: 4 }}>
                {tiles.length > 0 && (
                  <PersonTileDisplay tile={tiles[0]} onClick={() => toggle(tiles[0])}
                    selected={!!sels.find(s => s.id === tiles[0].id)}
                    disabled={!canSel(tiles[0]) && !sels.find(s => s.id === tiles[0].id)} />
                )}
                <span style={{ fontSize: 10, color: "#705030" }}>×{tiles.length}</span>
              </div>
            ))}
          </div>

          {sels.length > 0 && (
            <div style={{ marginBottom: 16 }}>
              <span style={{ color: "#d4a017", fontSize: 13 }}>Selected: </span>
              {sels.map(s => {
                const t = PERSON_TYPES[s.type_id];
                return (
                  <span key={s.id} style={{
                    display: "inline-block", background: `${t?.color}22`,
                    border: `1px solid ${t?.color}`, borderRadius: 6,
                    padding: "3px 10px", fontSize: 12, color: t?.color, marginRight: 6,
                  }}>{t?.icon} {t?.name} (+{s.value})</span>
                );
              })}
            </div>
          )}

          <button onClick={confirm} disabled={sels.length !== 2} style={bs(true, sels.length !== 2)}>Confirm</button>
        </div>
      ) : (
        <WaitingBanner phaseInfo={{ description: `${drafter?.name} is drafting...` }} players={players} waitingFor={[drafter?.player_id]} />
      )}

      <PlayerAreasGrid players={players} activePlayerIdx={drafterIdx} yourPlayerIdx={myIdx} />
    </div>
  );
}

// ─── ACTION PHASE ──────────────────────────────────────────────────

function BuildPlacementUI({ totalFloors, initialPalaces, onConfirm, onCancel, playerName }) {
  const [palaces, setPalaces] = useState(initialPalaces.map(p => ({ ...p, persons: [...(p.persons || [])] })));
  const [remaining, setRemaining] = useState(totalFloors);

  const addFloor = (palaceIdx) => {
    if (remaining <= 0 || palaces[palaceIdx].floors >= 3) return;
    const np = palaces.map((p, i) => i === palaceIdx ? { ...p, floors: p.floors + 1 } : p);
    setPalaces(np);
    setRemaining(remaining - 1);
  };

  const addNewPalace = () => {
    if (remaining <= 0) return;
    setPalaces([...palaces, { floors: 1, persons: [] }]);
    setRemaining(remaining - 1);
  };

  const resetBuild = () => {
    setPalaces(initialPalaces.map(p => ({ ...p, persons: [...(p.persons || [])] })));
    setRemaining(totalFloors);
  };

  // Build the placement array for the server
  const buildPlacement = () => {
    const placement = [];
    for (let i = 0; i < palaces.length; i++) {
      if (i < initialPalaces.length) {
        const added = palaces[i].floors - initialPalaces[i].floors;
        if (added > 0) placement.push({ palace_index: i, floors: added });
      } else {
        placement.push({ palace_index: "new", floors: palaces[i].floors });
      }
    }
    return placement;
  };

  return (
    <div style={{ padding: 16, background: "rgba(139,105,20,0.08)", border: "1px solid #8B691444", borderRadius: 8, marginBottom: 16 }}>
      <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", marginBottom: 12 }}>
        <h3 style={{ color: "#d4a017", fontSize: 16, margin: 0 }}>🏗️ {playerName}: Place {totalFloors} floor(s)</h3>
        <span style={{
          fontSize: 14, fontWeight: 700, padding: "4px 14px", borderRadius: 12,
          background: remaining === 0 ? "rgba(39,174,96,0.2)" : "rgba(212,160,23,0.2)",
          color: remaining === 0 ? "#27ae60" : "#d4a017",
          border: remaining === 0 ? "1px solid #27ae6044" : "1px solid #d4a01744",
        }}>{remaining === 0 ? "✓ All placed" : `${remaining} remaining`}</span>
      </div>

      <div style={{ display: "flex", gap: 10, flexWrap: "wrap", marginBottom: 16 }}>
        {palaces.map((pal, i) => {
          const canAdd = remaining > 0 && pal.floors < 3;
          return (
            <div key={i} style={{
              background: "rgba(0,0,0,0.15)", border: "1px solid #5a3a2066",
              borderRadius: 8, padding: 10, minWidth: 120, textAlign: "center",
            }}>
              <PalaceDisplay palace={pal} palaceIndex={i} />
              <button onClick={() => addFloor(i)} disabled={!canAdd}
                style={{ ...bs(false, !canAdd), fontSize: 11, padding: "4px 10px", marginTop: 6, width: "100%" }}>
                + Add Floor
              </button>
            </div>
          );
        })}
        {remaining > 0 && (
          <div onClick={addNewPalace} style={{
            background: "rgba(0,0,0,0.1)", border: "2px dashed #5a3a2066",
            borderRadius: 8, padding: 10, minWidth: 120, minHeight: 100,
            display: "flex", flexDirection: "column", alignItems: "center", justifyContent: "center",
            cursor: "pointer", gap: 6,
          }}>
            <span style={{ fontSize: 24, color: "#5a3a20" }}>+</span>
            <span style={{ fontSize: 11, color: "#806040" }}>New Palace</span>
          </div>
        )}
      </div>

      <div style={{ display: "flex", gap: 10 }}>
        <button onClick={() => onConfirm(buildPlacement())} disabled={remaining > 0} style={bs(true, remaining > 0)}>Confirm Build</button>
        <button onClick={resetBuild} style={bs(false)}>Reset</button>
        <button onClick={onCancel} style={bs(false)}>Cancel</button>
      </div>
    </div>
  );
}

function ActionPhase({ state, sendAction, yourTurn }) {
  const [selGroup, setSelGroup] = useState(null);
  const [selAction, setSelAction] = useState(null);
  const [privChoice, setPrivChoice] = useState(null);

  const players = state.players;
  const myIdx = state.your_player_idx;
  const a = state.action || {};
  const turnOrder = a.turn_order || [];
  const orderIdx = a.order_idx || 0;
  const groups = a.action_groups || [];
  const dragons = a.dragons || [];
  const curPIdx = turnOrder[orderIdx];
  const curP = players[curPIdx];
  const inBuild = state.sub_phase === "awaiting_build";

  const me = players[myIdx];
  const gCost = (g) => (dragons[g]?.length > 0) ? 3 : 0;
  const canAfford = (g) => !dragons[g]?.length || (me?.yuan >= 3);

  const handleConfirm = () => {
    if (selAction === "build") {
      // Server will put us into awaiting_build
      const cmd = { kind: "choose_action", group_index: selGroup, action_id: "build" };
      sendAction(cmd);
    } else if (selAction === "privilege") {
      if (!privChoice) return;
      sendAction({ kind: "choose_action", group_index: selGroup, action_id: "privilege", privilege_size: privChoice });
    } else {
      sendAction({ kind: "choose_action", group_index: selGroup, action_id: selAction });
    }
    setSelGroup(null);
    setSelAction(null);
    setPrivChoice(null);
  };

  const handleBuildConfirm = (placement) => {
    sendAction({ kind: "confirm_build", placement });
  };

  const handleSkip = () => {
    sendAction({ kind: "skip_action" });
    setSelGroup(null);
    setSelAction(null);
    setPrivChoice(null);
  };

  // If done
  if (orderIdx >= turnOrder.length) {
    return (
      <>
        <div style={S.card}>
          <h2 style={S.cardTitle}>Action Phase Complete</h2>
          <p style={{ color: "#a08060", fontSize: 13 }}>Advancing to next phase...</p>
        </div>
        <PlayerAreasGrid players={players} activePlayerIdx={-1} yourPlayerIdx={myIdx} />
      </>
    );
  }

  // Build sub-phase
  if (inBuild && yourTurn) {
    const bonus = countSymbols(me, "craftsman");
    const totalFloors = 1 + bonus;
    return (
      <>
        <TurnOrderBar players={players} turnOrder={turnOrder} currentOrderIdx={orderIdx} phaseName="Action" />
        <div style={S.card}>
          <BuildPlacementUI totalFloors={totalFloors} initialPalaces={me.palaces}
            onConfirm={handleBuildConfirm} onCancel={() => {/* can't cancel server-side easily, just place */}}
            playerName={me.name} />
        </div>
        <PlayerAreasGrid players={players} activePlayerIdx={curPIdx} yourPlayerIdx={myIdx} />
      </>
    );
  }

  if (!yourTurn) {
    return (
      <>
        <TurnOrderBar players={players} turnOrder={turnOrder} currentOrderIdx={orderIdx} phaseName="Action" />
        <WaitingBanner phaseInfo={{ description: `${curP?.name} is choosing an action...` }}
          players={players} waitingFor={[curP?.player_id]} />
        <PlayerAreasGrid players={players} activePlayerIdx={curPIdx} yourPlayerIdx={myIdx} />
      </>
    );
  }

  const yuanAfter = selGroup !== null ? me.yuan - gCost(selGroup) : 0;

  return (
    <>
      <TurnOrderBar players={players} turnOrder={turnOrder} currentOrderIdx={orderIdx} phaseName="Action" />
      <div style={S.card}>
        <div style={{ display: "flex", alignItems: "center", gap: 12, marginBottom: 16 }}>
          <div style={{ width: 20, height: 20, borderRadius: "50%", background: me?.color?.primary }} />
          <h2 style={{ ...S.cardTitle, margin: 0, border: "none", padding: 0 }}>Your Action</h2>
          <span style={{ fontSize: 12, color: "#a08060" }}>({me?.yuan}¥)</span>
        </div>

        <div style={{ display: "flex", gap: 12, flexWrap: "wrap", marginBottom: 16 }}>
          {groups.map((group, gIdx) => (
            <div key={gIdx} style={{
              borderRadius: 10, padding: 10,
              background: selGroup === gIdx ? "rgba(212,160,23,0.08)" : "rgba(20,10,5,0.5)",
              border: selGroup === gIdx ? "2px solid #d4a01788" : "1px solid #5a3a2066",
              opacity: canAfford(gIdx) ? 1 : 0.35,
              cursor: canAfford(gIdx) ? "pointer" : "not-allowed",
            }} onClick={() => { if (canAfford(gIdx)) { setSelGroup(gIdx); setSelAction(null); setPrivChoice(null); } }}>
              <div style={{ fontSize: 10, color: "#705030", marginBottom: 6, display: "flex", alignItems: "center", gap: 6 }}>
                <span>Group {gIdx + 1}</span>
                {(dragons[gIdx]?.length > 0) && (
                  <span style={{ color: "#c0392b", fontSize: 10 }}>🐉×{dragons[gIdx].length} — costs 3¥</span>
                )}
              </div>
              <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
                {group.map((aId, aIdx) => (
                  <ActionCard key={aIdx} actionId={aId}
                    selected={selGroup === gIdx && selAction === aId}
                    onClick={(e) => { e?.stopPropagation?.(); if (selGroup === gIdx) { setSelAction(aId); setPrivChoice(null); } }}
                    disabled={selGroup !== gIdx} player={me} />
                ))}
              </div>
            </div>
          ))}
        </div>

        {selAction === "privilege" && (
          <div style={{ marginBottom: 16, padding: 12, background: "rgba(0,0,0,0.2)", borderRadius: 6 }}>
            <span style={{ color: "#d4a017", fontSize: 13 }}>Choose privilege: </span>
            <button onClick={() => setPrivChoice("small")} disabled={yuanAfter < 2}
              style={{ ...bs(privChoice === "small", yuanAfter < 2), fontSize: 12, padding: "4px 12px", marginRight: 8 }}>
              Small (2¥) — 1🐉/round
            </button>
            <button onClick={() => setPrivChoice("large")} disabled={yuanAfter < 7}
              style={{ ...bs(privChoice === "large", yuanAfter < 7), fontSize: 12, padding: "4px 12px" }}>
              Large (7¥) — 2🐉/round
            </button>
          </div>
        )}

        <div style={{ display: "flex", gap: 12 }}>
          <button onClick={handleConfirm}
            disabled={selGroup === null || selAction === null || (selAction === "privilege" && !privChoice)}
            style={bs(true, selGroup === null || selAction === null || (selAction === "privilege" && !privChoice))}>
            Confirm Action
          </button>
          <button onClick={handleSkip} style={bs(false)}>Skip (top up to 3¥)</button>
        </div>
      </div>
      <PlayerAreasGrid players={players} activePlayerIdx={curPIdx} yourPlayerIdx={myIdx} />
    </>
  );
}

// ─── PERSON PHASE ──────────────────────────────────────────────────

function PersonPhase({ state, sendAction, yourTurn }) {
  const [selCard, setSelCard] = useState(null);
  const [selTile, setSelTile] = useState(null);
  const [selPalace, setSelPalace] = useState(null);
  const [replacing, setReplacing] = useState(null);
  const [releaseImmediate, setReleaseImmediate] = useState(false);

  const players = state.players;
  const myIdx = state.your_player_idx;
  const me = players[myIdx];
  const p = state.person || {};
  const turnOrder = p.turn_order || [];
  const orderIdx = p.order_idx || 0;
  const curPIdx = turnOrder[orderIdx];
  const curP = players[curPIdx];
  const tiles = state.remaining_tiles || [];

  const avail = useMemo(() => {
    if (selCard === null) return [];
    const card = me?.cards?.[selCard];
    if (!card) return [];
    return card.is_wild ? tiles : tiles.filter(t => t.type_id === card.type_id);
  }, [selCard, tiles, me]);

  const hasEmptySlot = (me?.palaces || []).some(pal => (pal.persons || []).length < pal.floors);
  const allFull = !hasEmptySlot;

  const canConf = selCard !== null && (
    avail.length === 0 ||
    releaseImmediate ||
    (selTile && selPalace !== null && (
      (me?.palaces?.[selPalace]?.persons || []).length < (me?.palaces?.[selPalace]?.floors || 0) ||
      replacing !== null
    ))
  );

  const confirmP = () => {
    const cmd = { kind: "play_person", card_index: selCard };
    if (selTile) {
      cmd.tile_id = selTile.id;
      if (releaseImmediate) {
        cmd.release_immediately = true;
      } else if (selPalace !== null) {
        cmd.palace_index = selPalace;
        if (replacing !== null) cmd.replace_index = replacing;
      }
    }
    sendAction(cmd);
    setSelCard(null); setSelTile(null); setSelPalace(null);
    setReplacing(null); setReleaseImmediate(false);
  };

  if (orderIdx >= turnOrder.length) {
    return (
      <>
        <div style={S.card}>
          <h2 style={S.cardTitle}>Person Phase Complete</h2>
          <p style={{ color: "#a08060", fontSize: 13 }}>Advancing to event phase...</p>
        </div>
        <PlayerAreasGrid players={players} activePlayerIdx={-1} yourPlayerIdx={myIdx} />
      </>
    );
  }

  if (!yourTurn) {
    return (
      <>
        <TurnOrderBar players={players} turnOrder={turnOrder} currentOrderIdx={orderIdx} phaseName="Person" />
        <WaitingBanner phaseInfo={{ description: `${curP?.name} is placing a person...` }}
          players={players} waitingFor={[curP?.player_id]} />
        <PlayerAreasGrid players={players} activePlayerIdx={curPIdx} yourPlayerIdx={myIdx} />
      </>
    );
  }

  return (
    <>
      <TurnOrderBar players={players} turnOrder={turnOrder} currentOrderIdx={orderIdx} phaseName="Person" />
      <div style={S.card}>
        <div style={{ display: "flex", alignItems: "center", gap: 12, marginBottom: 16 }}>
          <div style={{ width: 20, height: 20, borderRadius: "50%", background: me?.color?.primary }} />
          <h2 style={{ ...S.cardTitle, margin: 0, border: "none", padding: 0 }}>Your Turn — Play a Person Card</h2>
        </div>

        {/* Hand */}
        <div style={{ marginBottom: 16 }}>
          <span style={{ fontSize: 13, color: "#d4a017", display: "block", marginBottom: 8 }}>Your hand:</span>
          <div style={{ display: "flex", gap: 6, flexWrap: "wrap" }}>
            {(me?.cards || []).map((card, i) => {
              if (card.hidden) return null;
              return (
                <div key={i} onClick={() => {
                  setSelCard(i); setSelTile(null); setSelPalace(null);
                  setReplacing(null); setReleaseImmediate(false);
                }} style={{
                  padding: "6px 12px", borderRadius: 6, fontSize: 12, cursor: "pointer",
                  background: selCard === i ? "rgba(212,160,23,0.2)" : "rgba(30,15,8,0.6)",
                  border: selCard === i ? "2px solid #d4a017" : "1px solid #5a3a2088",
                }}>
                  {card.is_wild ? "❓ Wild" : `${PERSON_TYPES[card.type_id]?.icon} ${PERSON_TYPES[card.type_id]?.name}`}
                </div>
              );
            })}
          </div>
        </div>

        {/* Available tiles */}
        {selCard !== null && (
          <div style={{ marginBottom: 16 }}>
            <span style={{ fontSize: 13, color: "#d4a017", display: "block", marginBottom: 8 }}>
              Available tiles{me?.cards?.[selCard]?.is_wild ? " (any)" : ""}:
            </span>
            {avail.length === 0
              ? <p style={{ color: "#a08060", fontSize: 13 }}>None available. Card discarded.</p>
              : <div style={{ display: "flex", gap: 6, flexWrap: "wrap" }}>
                  {avail.map(t => <PersonTileDisplay key={t.id} tile={t}
                    onClick={() => { setSelTile(t); setSelPalace(null); setReplacing(null); setReleaseImmediate(false); }}
                    selected={selTile?.id === t.id} small />)}
                </div>
            }
          </div>
        )}

        {/* Palace placement */}
        {selTile && !releaseImmediate && (
          <div style={{ marginBottom: 16 }}>
            <span style={{ fontSize: 13, color: "#d4a017", display: "block", marginBottom: 8 }}>
              Place in palace{hasEmptySlot ? " (must use empty slot)" : ""}:
            </span>
            <div style={{ display: "flex", gap: 12, flexWrap: "wrap" }}>
              {(me?.palaces || []).map((pal, i) => {
                const isFull = (pal.persons || []).length >= pal.floors;
                const disabled = hasEmptySlot && isFull;
                return (
                  <div key={i} onClick={() => { if (!disabled) { setSelPalace(i); setReplacing(null); } }}
                    style={{
                      padding: 8, borderRadius: 6,
                      cursor: disabled ? "not-allowed" : "pointer", opacity: disabled ? 0.4 : 1,
                      border: selPalace === i ? "2px solid #d4a017" : "1px solid #5a3a2088",
                      background: selPalace === i ? "rgba(212,160,23,0.1)" : "rgba(30,15,8,0.6)",
                    }}>
                    <PalaceDisplay palace={pal} palaceIndex={i} />
                    {isFull && !hasEmptySlot && <span style={{ fontSize: 10, color: "#c0392b" }}>Full — replace</span>}
                    {isFull && hasEmptySlot && <span style={{ fontSize: 10, color: "#5a3a20" }}>Full</span>}
                    {!isFull && <span style={{ fontSize: 10, color: "#27ae60" }}>Space available</span>}
                  </div>
                );
              })}
            </div>
          </div>
        )}

        {/* Replace selection */}
        {selTile && !releaseImmediate && selPalace !== null && allFull && (
          <div style={{ marginBottom: 16 }}>
            <span style={{ fontSize: 13, color: "#c0392b", display: "block", marginBottom: 8 }}>Choose courtier to replace:</span>
            <div style={{ display: "flex", gap: 6, flexWrap: "wrap" }}>
              {(me?.palaces?.[selPalace]?.persons || []).map((per, i) => (
                <PersonTileDisplay key={i} tile={per} onClick={() => setReplacing(i)} selected={replacing === i} small />
              ))}
            </div>
          </div>
        )}

        {/* Release immediately option */}
        {selTile && allFull && (
          <div style={{ marginBottom: 16 }}>
            <div onClick={() => {
              setReleaseImmediate(!releaseImmediate);
              if (!releaseImmediate) { setSelPalace(null); setReplacing(null); }
            }} style={{
              display: "inline-flex", alignItems: "center", gap: 8,
              padding: "8px 16px", borderRadius: 6, cursor: "pointer",
              background: releaseImmediate ? "rgba(192,57,43,0.15)" : "rgba(0,0,0,0.15)",
              border: releaseImmediate ? "2px solid #c0392b" : "1px solid #5a3a2088",
            }}>
              <span style={{ fontSize: 14 }}>🚫</span>
              <div>
                <div style={{ fontSize: 12, color: releaseImmediate ? "#e74c3c" : "#a08060", fontWeight: 600 }}>Release immediately</div>
                <div style={{ fontSize: 10, color: "#806040" }}>Tile discarded — no person track advancement</div>
              </div>
            </div>
          </div>
        )}

        <button onClick={confirmP} disabled={!canConf} style={bs(true, !canConf)}>Confirm</button>
      </div>
      <PlayerAreasGrid players={players} activePlayerIdx={curPIdx} yourPlayerIdx={myIdx} />
    </>
  );
}

// ─── EVENT PHASE ───────────────────────────────────────────────────

function EventPhase({ state, sendAction, yourTurn }) {
  const players = state.players;
  const myIdx = state.your_player_idx;
  const ev = state.event || {};
  const eventTile = state.events?.[state.current_round];
  const elog = ev.log || [];

  // Drought feeding
  const droughtQueue = ev.drought_queue || [];
  const curDrought = droughtQueue[0];

  // Generic release
  const releaseQueue = ev.release_queue || [];
  const curRelease = releaseQueue[0];

  const [fedSet, setFedSet] = useState(new Set());
  const [relSel, setRelSel] = useState(null);
  const [droughtRelSel, setDroughtRelSel] = useState(null);

  // Reset selections when queue changes
  useEffect(() => { setRelSel(null); }, [curRelease?.player_idx, curRelease?.count]);
  useEffect(() => { setDroughtRelSel(null); setFedSet(new Set()); }, [curDrought?.player_idx, curDrought?.phase]);

  const needsResolve = !ev.resolved && elog.length === 0 && !curDrought && !curRelease;
  const activePI = curDrought ? curDrought.player_idx : curRelease ? curRelease.player_idx : -1;

  // Drought feed setup
  let feedInfo = null;
  if (curDrought?.phase === "feed") {
    const p = players[curDrought.player_idx];
    const inhabited = (p?.palaces || []).map((pal, i) => ({ idx: i, count: (pal.persons || []).length })).filter(x => x.count > 0);
    const maxFeed = Math.min(p?.rice || 0, inhabited.length);
    feedInfo = { player: p, inhabited, maxFeed };
  }

  const toggleFeed = (palaceIdx) => {
    if (!feedInfo) return;
    const nf = new Set(fedSet);
    if (nf.has(palaceIdx)) nf.delete(palaceIdx);
    else if (nf.size < feedInfo.maxFeed) nf.add(palaceIdx);
    setFedSet(nf);
  };

  return (
    <>
      <div style={S.card}>
        <h2 style={S.cardTitle}>{eventTile?.icon} {eventTile?.name} — Month {(state.current_round || 0) + 1}</h2>

        {needsResolve && yourTurn && (
          <button onClick={() => sendAction({ kind: "resolve_event" })} style={bs(true)}>Resolve Event</button>
        )}
        {needsResolve && !yourTurn && (
          <p style={{ color: "#a08060", fontSize: 13 }}>Waiting for event resolution...</p>
        )}

        {elog.length > 0 && (
          <div style={{ marginBottom: 16 }}>
            {elog.map((m, i) => <p key={i} style={{ fontSize: 13, color: "#c0a070", margin: "4px 0" }}>• {m}</p>)}
          </div>
        )}

        {/* Drought feeding */}
        {curDrought?.phase === "feed" && curDrought.player_idx === myIdx && feedInfo && (
          <div style={{ padding: 16, background: "rgba(230,126,34,0.08)", border: "1px solid #e67e2244", borderRadius: 8, marginBottom: 16 }}>
            <h3 style={{ color: "#e67e22", fontSize: 16, margin: "0 0 8px" }}>☀️ Drought — Feed your palaces</h3>
            <p style={{ fontSize: 13, color: "#c0a070", marginBottom: 12 }}>
              {feedInfo.player.rice} rice available, {feedInfo.inhabited.length} inhabited palace(s).
              {feedInfo.maxFeed >= feedInfo.inhabited.length ? " You have enough to feed all."
                : ` Choose ${feedInfo.maxFeed} palace(s) to feed.`}
            </p>
            <div style={{ display: "flex", gap: 10, flexWrap: "wrap", marginBottom: 16 }}>
              {feedInfo.inhabited.map(({ idx }) => {
                const pal = feedInfo.player.palaces[idx];
                const isFed = fedSet.has(idx);
                return (
                  <div key={idx} onClick={() => toggleFeed(idx)} style={{
                    background: isFed ? "rgba(39,174,96,0.1)" : "rgba(192,57,43,0.08)",
                    border: isFed ? "2px solid #27ae60" : "2px solid #c0392b44",
                    borderRadius: 8, padding: 10, cursor: "pointer", minWidth: 120, textAlign: "center",
                  }}>
                    <PalaceDisplay palace={pal} palaceIndex={idx} />
                    <div style={{
                      marginTop: 6, fontSize: 11, fontWeight: 700, padding: "3px 8px", borderRadius: 4,
                      background: isFed ? "rgba(39,174,96,0.2)" : "rgba(192,57,43,0.15)",
                      color: isFed ? "#27ae60" : "#c0392b",
                    }}>{isFed ? "🌾 Fed" : "☠️ Unfed"}</div>
                  </div>
                );
              })}
            </div>
            <button onClick={() => { sendAction({ kind: "feed_palaces", fed_palaces: [...fedSet] }); setFedSet(new Set()); }}
              disabled={fedSet.size !== feedInfo.maxFeed}
              style={bs(true, fedSet.size !== feedInfo.maxFeed)}>
              Confirm Feeding ({fedSet.size}/{feedInfo.maxFeed})
            </button>
          </div>
        )}
        {curDrought?.phase === "feed" && curDrought.player_idx !== myIdx && (
          <WaitingBanner phaseInfo={{ description: `${players[curDrought.player_idx]?.name} is feeding palaces...` }}
            players={players} waitingFor={[players[curDrought.player_idx]?.player_id]} />
        )}

        {/* Drought release */}
        {curDrought?.phase === "release" && curDrought.player_idx === myIdx && (
          <div style={{ padding: 16, background: "rgba(192,57,43,0.1)", border: "1px solid #c0392b44", borderRadius: 6, marginBottom: 16 }}>
            <p style={{ color: "#e74c3c", fontWeight: 600, marginBottom: 4 }}>
              Release 1 courtier from unfed Palace {(curDrought.unfed_palaces?.[0] ?? 0) + 1}
            </p>
            <p style={{ color: "#a08060", fontSize: 12, marginBottom: 12 }}>{(curDrought.unfed_palaces || []).length} unfed palace(s) remaining</p>
            <div style={{ display: "flex", gap: 10, flexWrap: "wrap", marginBottom: 12 }}>
              {(players[myIdx]?.palaces?.[curDrought.unfed_palaces?.[0]]?.persons || []).map((per, peI) => (
                <PersonTileDisplay key={peI} tile={per} onClick={() => setDroughtRelSel(peI)}
                  selected={droughtRelSel === peI} small />
              ))}
            </div>
            <button onClick={() => {
              sendAction({ kind: "release_person", palace_index: curDrought.unfed_palaces[0], person_index: droughtRelSel });
              setDroughtRelSel(null);
            }} disabled={droughtRelSel === null} style={bs(true, droughtRelSel === null)}>Release Selected</button>
          </div>
        )}
        {curDrought?.phase === "release" && curDrought.player_idx !== myIdx && (
          <WaitingBanner phaseInfo={{ description: `${players[curDrought.player_idx]?.name} is releasing a courtier...` }}
            players={players} waitingFor={[players[curDrought.player_idx]?.player_id]} />
        )}

        {/* Generic release (tribute, contagion, mongols) */}
        {curRelease && curRelease.player_idx === myIdx && (
          <div style={{ padding: 16, background: "rgba(192,57,43,0.1)", border: "1px solid #c0392b44", borderRadius: 6, marginBottom: 16 }}>
            <p style={{ color: "#e74c3c", fontWeight: 600, marginBottom: 8 }}>
              Release courtier ({curRelease.reason}) — {curRelease.count} left
            </p>
            <div style={{ display: "flex", gap: 12, flexWrap: "wrap" }}>
              {(players[myIdx]?.palaces || []).map((pal, pi) =>
                (pal.persons || []).map((per, peI) => (
                  <PersonTileDisplay key={`${pi}-${peI}`} tile={per}
                    onClick={() => setRelSel({ palaceIdx: pi, personIdx: peI })}
                    selected={relSel?.palaceIdx === pi && relSel?.personIdx === peI} small />
                ))
              )}
            </div>
            <button onClick={() => {
              sendAction({ kind: "release_person", palace_index: relSel.palaceIdx, person_index: relSel.personIdx });
              setRelSel(null);
            }} disabled={!relSel} style={{ ...bs(true, !relSel), marginTop: 12 }}>Release</button>
          </div>
        )}
        {curRelease && curRelease.player_idx !== myIdx && (
          <WaitingBanner phaseInfo={{ description: `${players[curRelease.player_idx]?.name} is releasing a courtier...` }}
            players={players} waitingFor={[players[curRelease.player_idx]?.player_id]} />
        )}
      </div>
      <PlayerAreasGrid players={players} activePlayerIdx={activePI} yourPlayerIdx={myIdx} />
    </>
  );
}

// ─── SCORING PHASE ─────────────────────────────────────────────────

function ScoringPhase({ state, sendAction }) {
  const players = state.players;
  const myIdx = state.your_player_idx;
  const sc = state.scoring || {};

  return (
    <>
      <div style={S.card}>
        <h2 style={S.cardTitle}>⭐ Scoring Phase</h2>
        {!sc.scored ? (
          <button onClick={() => sendAction({ kind: "score" })} style={bs(true)}>Calculate Scores</button>
        ) : (
          <>
            <div style={{ marginBottom: 16 }}>
              {(sc.details || []).map(d => (
                <p key={d.player_idx} style={{ fontSize: 13, color: "#c0a070", margin: "4px 0" }}>
                  {players[d.player_idx]?.name}: +{d.total} VP ({d.palaces} palaces, {d.ladies} ladies, {d.privileges} privileges)
                </p>
              ))}
            </div>
            <button onClick={() => sendAction({ kind: "next_round" })} style={bs(true)}>
              {state.current_round < 11 ? "Next Round →" : "Final Scoring →"}
            </button>
          </>
        )}
      </div>
      <PlayerAreasGrid players={players} activePlayerIdx={-1} yourPlayerIdx={myIdx} />
    </>
  );
}

// ─── FINAL SCORING ─────────────────────────────────────────────────

function FinalScoring({ state }) {
  const results = state.final_results || [];
  const players = state.players;
  const myIdx = state.your_player_idx;

  return (
    <div style={S.content}>
      <div style={{ textAlign: "center", padding: "30px 0 20px", borderBottom: "2px solid #8B4513", marginBottom: 30 }}>
        <h1 style={S.title}>🐉 Game Over! 🐉</h1>
      </div>
      {results.map((r, rank) => {
        const p = players[r.player_idx];
        const isYou = r.player_idx === myIdx;
        return (
          <div key={r.player_idx} style={{
            ...S.card,
            border: rank === 0 ? "2px solid #d4a017" : isYou ? `1px solid ${p?.color?.primary}66` : S.card.border,
            boxShadow: rank === 0 ? "0 0 30px rgba(212,160,23,0.3)" : S.card.boxShadow,
          }}>
            <div style={{ display: "flex", alignItems: "center", gap: 12, marginBottom: 12 }}>
              <span style={{ fontSize: 28, fontWeight: 700, color: rank === 0 ? "#d4a017" : rank === 1 ? "#c0c0c0" : "#cd7f32" }}>
                #{rank + 1}
              </span>
              <div style={{ width: 20, height: 20, borderRadius: "50%", background: p?.color?.primary }} />
              <span style={{ fontSize: 20, color: p?.color?.light, fontWeight: 700 }}>
                {r.name} {isYou ? "(you)" : ""}
              </span>
              <span style={{ fontSize: 24, fontWeight: 700, color: "#d4a017", marginLeft: "auto" }}>{r.final_score} VP</span>
            </div>
            <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
              <ResourceBadge icon="⭐" label="Game Score" value={r.game_score} />
              <ResourceBadge icon="👥" label="Persons" value={`+${r.person_pts}`} />
              <ResourceBadge icon="☸️" label="Monks" value={`+${r.monk_pts}`} />
              <ResourceBadge icon="💰" label="Money" value={`+${r.money_pts}`} />
              <ResourceBadge icon="👤" label="Track" value={r.person_track} />
            </div>
          </div>
        );
      })}
    </div>
  );
}

// ─── MAIN APP ──────────────────────────────────────────────────────

export default function App() {
  const conn = useGameConnection();
  const state = conn.gameState;
  const phase = state?.phase;

  // Not in a game yet → lobby
  if (!conn.gameStarted || !state) {
    return (
      <div style={S.app}>
        <div style={S.overlay} />
        <LobbyScreen conn={conn} />
      </div>
    );
  }

  // Final scoring
  if (phase === "final") {
    return (
      <div style={S.app}>
        <div style={S.overlay} />
        <FinalScoring state={state} />
      </div>
    );
  }

  // Draft
  if (phase === "draft") {
    return (
      <div style={S.app}>
        <div style={S.overlay} />
        <DraftPhase state={state} sendAction={conn.sendAction} yourTurn={conn.yourTurn} />
      </div>
    );
  }

  // Main game phases
  return (
    <div style={S.app}>
      <div style={S.overlay} />
      <div style={S.content}>
        {/* Title bar */}
        <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", borderBottom: "2px solid #8B4513", paddingBottom: 12, marginBottom: 12 }}>
          <h1 style={{ ...S.title, fontSize: 28 }}>🐉 Year of the Dragon</h1>
          <div style={{ display: "flex", alignItems: "center", gap: 16 }}>
            <div style={{ color: "#d4a017", fontSize: 16, fontWeight: 700 }}>Round {(state.current_round || 0) + 1}/12</div>
            <div style={{
              fontSize: 10, padding: "3px 8px", borderRadius: 4,
              background: conn.connected ? "rgba(39,174,96,0.2)" : "rgba(192,57,43,0.2)",
              color: conn.connected ? "#27ae60" : "#c0392b",
              border: conn.connected ? "1px solid #27ae6044" : "1px solid #c0392b44",
            }}>{conn.connected ? "● Online" : "○ Reconnecting..."}</div>
          </div>
        </div>

        {/* Error banner */}
        {conn.error && (
          <div style={{ padding: "8px 16px", marginBottom: 12, borderRadius: 6, background: "rgba(192,57,43,0.15)", border: "1px solid #c0392b44" }}>
            <span style={{ fontSize: 13, color: "#e74c3c" }}>⚠ {conn.error}</span>
          </div>
        )}

        {/* Phase tracker */}
        <div style={{ marginBottom: 12 }}><PhaseTracker currentPhase={phase} /></div>

        {/* Event track */}
        <div style={{ ...S.card, padding: 12 }}>
          <div style={{ fontSize: 12, color: "#d4a017", marginBottom: 6 }}>Event Track</div>
          <EventTrack events={state.events} currentRound={state.current_round} />
        </div>

        {/* Game log */}
        <GameLog logs={conn.gameLogs} />

        {/* Phase content */}
        {phase === "action" && <ActionPhase state={state} sendAction={conn.sendAction} yourTurn={conn.yourTurn} />}
        {phase === "person" && <PersonPhase state={state} sendAction={conn.sendAction} yourTurn={conn.yourTurn} />}
        {phase === "event" && <EventPhase state={state} sendAction={conn.sendAction} yourTurn={conn.yourTurn} />}
        {phase === "scoring" && <ScoringPhase state={state} sendAction={conn.sendAction} />}
      </div>
    </div>
  );
}
