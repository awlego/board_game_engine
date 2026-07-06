import React, { useState, useEffect, useRef, useCallback, useMemo } from "react";

// ─── CONFIGURATION ─────────────────────────────────────────────────
import { WS_URL } from "../ws.js";

// ─── CONSTANTS ─────────────────────────────────────────────────────

const EXPEDITIONS = ["yellow", "blue", "white", "green", "red"];

const EXP_INFO = {
  yellow: { name: "Desert",     hex: "#e6a817", light: "#fdf5d4", icon: "🏜️" },
  blue:   { name: "Sea",        hex: "#2980b9", light: "#daeaf6", icon: "🌊" },
  white:  { name: "Himalayas",  hex: "#bdc3c7", light: "#ecf0f1", icon: "🏔️" },
  green:  { name: "Rainforest", hex: "#27ae60", light: "#d4efdf", icon: "🌿" },
  red:    { name: "Volcano",    hex: "#c0392b", light: "#fce4e4", icon: "🌋" },
};

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
  content: { position: "relative", zIndex: 1, maxWidth: 1000, margin: "0 auto", padding: "16px 20px" },
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
  if (!state || !state.players) return 0;
  const idx = state.players.findIndex(p => p.player_id === state.your_player_id);
  return idx >= 0 ? idx : 0;
}

function cardLabel(card) {
  if (card.value === 0) return "×";
  return String(card.value);
}

function cardSortKey(card) {
  const expOrder = EXPEDITIONS.indexOf(card.expedition);
  return expOrder * 100 + card.value;
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


  const createRoom = (name) => {
    connect(() => send({ type: "create", game: "lostcities", name }));
  };
  const joinRoom = (code, name) => {
    connect(() => send({ type: "join", room_code: code.toUpperCase(), name }));
  };

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

// ─── CARD COMPONENT ────────────────────────────────────────────────

function Card({ card, onClick, selected, small, faceDown }) {
  const exp = EXP_INFO[card?.expedition] || {};
  const isWager = card?.value === 0;
  const w = small ? 52 : 68;
  const h = small ? 78 : 100;

  if (faceDown) {
    return (
      <div style={{
        width: w, height: h, borderRadius: 6,
        background: "linear-gradient(135deg, #2c1810 0%, #1a0f08 100%)",
        border: "2px solid #5c3a1e",
        display: "flex", alignItems: "center", justifyContent: "center",
        fontSize: small ? 16 : 22, color: "#5c3a1e",
      }}>
        ?
      </div>
    );
  }

  return (
    <div
      onClick={onClick}
      style={{
        width: w, height: h, borderRadius: 6,
        background: `linear-gradient(135deg, ${exp.hex || "#555"}dd, ${exp.hex || "#555"}99)`,
        border: `2px solid ${selected ? "#fff" : exp.hex || "#555"}`,
        boxShadow: selected ? "0 0 12px rgba(255,255,255,0.5)" : "0 2px 8px rgba(0,0,0,0.4)",
        display: "flex", flexDirection: "column", alignItems: "center", justifyContent: "center",
        cursor: onClick ? "pointer" : "default",
        transition: "all 0.15s",
        transform: selected ? "translateY(-6px)" : "none",
        color: "#fff", fontFamily: font, fontWeight: 700,
        textShadow: "0 1px 3px rgba(0,0,0,0.6)",
        position: "relative",
      }}
    >
      <div style={{ fontSize: small ? 18 : 26, lineHeight: 1 }}>
        {isWager ? "×" : card.value}
      </div>
      <div style={{ fontSize: small ? 8 : 10, marginTop: 2, opacity: 0.8 }}>
        {isWager ? "WAGER" : exp.name}
      </div>
    </div>
  );
}

// ─── EXPEDITION COLUMN ─────────────────────────────────────────────

function ExpeditionColumn({ cards, expedition, isOpponent }) {
  const exp = EXP_INFO[expedition];
  if (!cards || cards.length === 0) {
    return (
      <div style={{
        width: 56, minHeight: 40, borderRadius: 6,
        border: `1px dashed ${exp.hex}44`,
        display: "flex", alignItems: "center", justifyContent: "center",
        fontSize: 10, color: "#666", padding: 4,
      }}>
        —
      </div>
    );
  }

  const ordered = isOpponent ? [...cards].reverse() : cards;

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 0 }}>
      {ordered.map((card, i) => (
        <div key={card.id} style={{ marginTop: i > 0 ? -58 : 0, zIndex: i }}>
          <Card card={card} small />
        </div>
      ))}
    </div>
  );
}

// ─── DISCARD PILE ──────────────────────────────────────────────────

function DiscardPile({ pile, expedition, onClick, highlight }) {
  const exp = EXP_INFO[expedition];
  const top = pile?.top;
  const count = pile?.count || 0;

  return (
    <div
      onClick={top && onClick ? onClick : undefined}
      style={{
        width: 56, height: 82, borderRadius: 6,
        border: `2px ${highlight ? "solid" : "dashed"} ${highlight ? "#fff" : exp.hex + "66"}`,
        background: top ? `${exp.hex}33` : "rgba(0,0,0,0.2)",
        display: "flex", flexDirection: "column", alignItems: "center", justifyContent: "center",
        cursor: top && onClick ? "pointer" : "default",
        position: "relative",
        boxShadow: highlight ? "0 0 10px rgba(255,255,255,0.3)" : "none",
      }}
    >
      {top ? (
        <>
          <div style={{ fontSize: 18, fontWeight: 700, color: exp.hex }}>
            {top.value === 0 ? "×" : top.value}
          </div>
          <div style={{ fontSize: 8, color: "#999" }}>
            {top.value === 0 ? "WAGER" : ""}
          </div>
        </>
      ) : (
        <div style={{ fontSize: 18, color: "#333" }}>{exp.icon}</div>
      )}
      {count > 0 && (
        <div style={{
          position: "absolute", bottom: 2, right: 4,
          fontSize: 9, color: "#888",
        }}>
          {count}
        </div>
      )}
    </div>
  );
}

// ─── LOBBY ─────────────────────────────────────────────────────────

function Lobby({ game }) {
  const [name, setName] = useState("");
  const [joinCode, setJoinCode] = useState("");
  const [mode, setMode] = useState(null); // null | "create" | "join"

  if (game.roomCode) {
    return (
      <div style={S.app}>
        <div style={S.overlay} />
        <div style={S.content}>
          <h1 style={{ ...S.title, textAlign: "center", marginBottom: 24 }}>Lost Cities</h1>
          <div style={S.card}>
            <div style={S.cardTitle}>
              Room: <span style={{ letterSpacing: 3, fontSize: 22 }}>{game.roomCode}</span>
            </div>
            <div style={{ marginBottom: 16 }}>
              <div style={{ color: "#999", fontSize: 13, marginBottom: 8 }}>Players:</div>
              {game.lobby.map((p) => (
                <div key={p.player_id} style={{
                  padding: "6px 12px", marginBottom: 4, borderRadius: 4,
                  background: p.connected ? "rgba(39,174,96,0.15)" : "rgba(231,76,60,0.15)",
                  border: `1px solid ${p.connected ? "#27ae6044" : "#e74c3c44"}`,
                  fontSize: 14,
                }}>
                  {p.name} {p.is_host ? "👑" : ""} {!p.connected ? "(disconnected)" : ""}
                </div>
              ))}
            </div>
            {game.isHost && game.lobby.length >= 2 && (
              <button style={bs(true)} onClick={game.startGame}>
                Start Game
              </button>
            )}
            {game.isHost && game.lobby.length < 2 && (
              <div style={{ color: "#888", fontSize: 13 }}>
                Waiting for another player to join...
              </div>
            )}
            {!game.isHost && (
              <div style={{ color: "#888", fontSize: 13 }}>
                Waiting for host to start...
              </div>
            )}
          </div>
          {game.error && (
            <div style={{ color: "#e74c3c", padding: 12, background: "rgba(231,76,60,0.1)", borderRadius: 6 }}>
              {game.error}
            </div>
          )}
        </div>
      </div>
    );
  }

  return (
    <div style={S.app}>
      <div style={S.overlay} />
      <div style={S.content}>
        <h1 style={{ ...S.title, textAlign: "center", marginBottom: 8 }}>Lost Cities</h1>
        <p style={{ textAlign: "center", color: "#999", marginBottom: 32, fontSize: 14 }}>
          Explore ancient expeditions with a rival adventurer
        </p>
        {!mode && (
          <div style={{ ...S.card, display: "flex", gap: 12, justifyContent: "center" }}>
            <button style={bs(true)} onClick={() => setMode("create")}>Create Room</button>
            <button style={bs(false)} onClick={() => setMode("join")}>Join Room</button>
          </div>
        )}
        {mode === "create" && (
          <div style={S.card}>
            <div style={S.cardTitle}>Create a Room</div>
            <div style={{ display: "flex", gap: 8 }}>
              <input style={S.input} placeholder="Your name" value={name}
                onChange={(e) => setName(e.target.value)}
                onKeyDown={(e) => e.key === "Enter" && name.trim() && game.createRoom(name.trim())} />
              <button style={bs(true, !name.trim())} disabled={!name.trim()}
                onClick={() => game.createRoom(name.trim())}>Create</button>
            </div>
          </div>
        )}
        {mode === "join" && (
          <div style={S.card}>
            <div style={S.cardTitle}>Join a Room</div>
            <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
              <input style={{ ...S.input, maxWidth: 140 }} placeholder="Room code"
                value={joinCode} onChange={(e) => setJoinCode(e.target.value.toUpperCase())} />
              <input style={S.input} placeholder="Your name" value={name}
                onChange={(e) => setName(e.target.value)}
                onKeyDown={(e) => e.key === "Enter" && name.trim() && joinCode.trim() && game.joinRoom(joinCode.trim(), name.trim())} />
              <button style={bs(true, !name.trim() || !joinCode.trim())}
                disabled={!name.trim() || !joinCode.trim()}
                onClick={() => game.joinRoom(joinCode.trim(), name.trim())}>Join</button>
            </div>
          </div>
        )}
        {mode && (
          <button style={{ ...S.btn, marginTop: 8 }} onClick={() => setMode(null)}>Back</button>
        )}
        {game.error && (
          <div style={{ color: "#e74c3c", padding: 12, marginTop: 12, background: "rgba(231,76,60,0.1)", borderRadius: 6 }}>
            {game.error}
          </div>
        )}
      </div>
    </div>
  );
}

// ─── GAME BOARD ────────────────────────────────────────────────────

function GameBoard({ game }) {
  const { gameState: state, phaseInfo, yourTurn, submitAction, gameLogs, gameOver } = game;
  const [selectedCard, setSelectedCard] = useState(null);
  const logRef = useRef(null);

  const myIdx = useMemo(() => getMyPlayerIdx(state), [state]);
  const oppIdx = 1 - myIdx;
  if (!state.players || !state.players[myIdx]) {
    return <div style={S.app}><div style={S.content}><div style={S.card}>Loading game...</div></div></div>;
  }
  const me = state.players[myIdx];
  const opp = state.players[oppIdx];

  const sortedHand = useMemo(() => {
    if (!Array.isArray(me.hand)) return [];
    return [...me.hand].sort((a, b) => cardSortKey(a) - cardSortKey(b));
  }, [me.hand]);

  const validActions = state.valid_actions || [];
  const phase = state.phase;

  // Which expeditions can the selected card be played on?
  const playableAction = useMemo(() => {
    if (!selectedCard) return null;
    return validActions.find(
      (a) => a.kind === "play" && a.card_id === selectedCard.id
    );
  }, [selectedCard, validActions]);

  const discardableAction = useMemo(() => {
    if (!selectedCard) return null;
    return validActions.find(
      (a) => a.kind === "discard" && a.card_id === selectedCard.id
    );
  }, [selectedCard, validActions]);

  // Draw sources
  const drawActions = useMemo(() => {
    return validActions.filter((a) => a.kind === "draw");
  }, [validActions]);

  useEffect(() => {
    if (logRef.current) logRef.current.scrollTop = logRef.current.scrollHeight;
  }, [gameLogs]);

  // Clear selection when phase changes
  useEffect(() => { setSelectedCard(null); }, [phase]);

  const handlePlayCard = () => {
    if (playableAction) {
      submitAction(playableAction);
      setSelectedCard(null);
    }
  };

  const handleDiscardCard = () => {
    if (discardableAction) {
      submitAction(discardableAction);
      setSelectedCard(null);
    }
  };

  const handleDraw = (action) => {
    submitAction(action);
  };

  // ── Scoring display ──
  if (state.scoring) {
    const scores = state.scoring;
    return (
      <div style={S.app}>
        <div style={S.overlay} />
        <div style={S.content}>
          <h1 style={{ ...S.title, textAlign: "center", marginBottom: 8 }}>Game Over</h1>
          <div style={{ textAlign: "center", color: "#999", marginBottom: 24, fontSize: 14 }}>
            {state.winner === "draw" ? "It's a draw!" :
             `${state.players[state.winner].name} wins!`}
          </div>
          {state.players.map((p, pi) => (
            <div key={pi} style={{ ...S.card, marginBottom: 20 }}>
              <div style={S.cardTitle}>
                {p.name}: {scores[pi].total} points
                {state.winner === pi ? " 🏆" : ""}
              </div>
              <div style={{ display: "flex", gap: 12, flexWrap: "wrap" }}>
                {EXPEDITIONS.map((exp) => {
                  const s = scores[pi].expedition_scores[exp];
                  const info = EXP_INFO[exp];
                  return (
                    <div key={exp} style={{
                      padding: "8px 12px", borderRadius: 6, minWidth: 110,
                      background: `${info.hex}22`,
                      border: `1px solid ${info.hex}44`,
                    }}>
                      <div style={{ fontSize: 13, color: info.hex, fontWeight: 700, marginBottom: 4 }}>
                        {info.icon} {info.name}
                      </div>
                      {s.card_count === 0 ? (
                        <div style={{ fontSize: 12, color: "#666" }}>Not started</div>
                      ) : (
                        <div style={{ fontSize: 11, color: "#bbb", lineHeight: 1.5 }}>
                          Cards: {s.card_count}<br />
                          Sum - 20 = {s.subtotal}<br />
                          {s.wager_count > 0 && <>× {s.multiplier} = {s.result}<br /></>}
                          {s.bonus > 0 && <>+20 bonus<br /></>}
                          <span style={{ color: s.total >= 0 ? "#27ae60" : "#e74c3c", fontWeight: 700 }}>
                            = {s.total}
                          </span>
                        </div>
                      )}
                    </div>
                  );
                })}
              </div>
            </div>
          ))}
          <GameLog logs={gameLogs} logRef={logRef} />
        </div>
      </div>
    );
  }

  // ── Main game board ──
  return (
    <div style={S.app}>
      <div style={S.overlay} />
      <div style={S.content}>
        {/* Header */}
        <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 12 }}>
          <h1 style={{ ...S.title, fontSize: 24 }}>Lost Cities</h1>
          <div style={{ display: "flex", gap: 12, alignItems: "center" }}>
            <div style={{
              padding: "4px 12px", borderRadius: 12, fontSize: 12,
              background: yourTurn ? "rgba(39,174,96,0.2)" : "rgba(255,255,255,0.05)",
              border: `1px solid ${yourTurn ? "#27ae60" : "#30363d"}`,
              color: yourTurn ? "#27ae60" : "#888",
            }}>
              {yourTurn ? (phase === "play" ? "Play a card" : "Draw a card") : `${opp.name}'s turn`}
            </div>
            <div style={{ fontSize: 12, color: "#888" }}>
              Draw: {state.draw_pile_count}
            </div>
          </div>
        </div>

        {/* Opponent expeditions */}
        <div style={S.card}>
          <div style={{ ...S.cardTitle, fontSize: 14 }}>
            {opp.name}'s Expeditions
            <span style={{ color: "#888", fontWeight: 400, marginLeft: 8 }}>
              ({typeof opp.hand === "number" ? opp.hand : "?"} cards in hand)
            </span>
          </div>
          <div style={{ display: "flex", gap: 16, justifyContent: "center" }}>
            {EXPEDITIONS.map((exp) => (
              <div key={exp} style={{ textAlign: "center" }}>
                <div style={{ fontSize: 10, color: EXP_INFO[exp].hex, marginBottom: 4, fontWeight: 600 }}>
                  {EXP_INFO[exp].icon} {EXP_INFO[exp].name}
                </div>
                <ExpeditionColumn cards={opp.expeditions[exp]} expedition={exp} isOpponent />
              </div>
            ))}
          </div>
        </div>

        {/* Discard piles (center board) */}
        <div style={{ ...S.card, textAlign: "center" }}>
          <div style={{ ...S.cardTitle, fontSize: 14 }}>
            {phase === "draw" && yourTurn ? "Choose a card to draw" : "Discard Piles"}
          </div>
          <div style={{ display: "flex", gap: 16, justifyContent: "center", alignItems: "center" }}>
            {EXPEDITIONS.map((exp) => {
              const drawFromDiscard = drawActions.find(
                (a) => a.source === "discard" && a.expedition === exp
              );
              return (
                <div key={exp} style={{ textAlign: "center" }}>
                  <DiscardPile
                    pile={state.discard_piles[exp]}
                    expedition={exp}
                    onClick={drawFromDiscard ? () => handleDraw(drawFromDiscard) : undefined}
                    highlight={!!drawFromDiscard && yourTurn && phase === "draw"}
                  />
                </div>
              );
            })}
            {/* Draw pile */}
            {phase === "draw" && yourTurn && drawActions.find((a) => a.source === "draw_pile") && (
              <div style={{ marginLeft: 16 }}>
                <div
                  onClick={() => handleDraw(drawActions.find((a) => a.source === "draw_pile"))}
                  style={{
                    width: 56, height: 82, borderRadius: 6,
                    background: "linear-gradient(135deg, #2c1810 0%, #1a0f08 100%)",
                    border: "2px solid #c9a84c",
                    display: "flex", flexDirection: "column", alignItems: "center", justifyContent: "center",
                    cursor: "pointer", boxShadow: "0 0 10px rgba(201,168,76,0.3)",
                  }}
                >
                  <div style={{ fontSize: 14, color: "#c9a84c", fontWeight: 700 }}>Draw</div>
                  <div style={{ fontSize: 10, color: "#888" }}>{state.draw_pile_count}</div>
                </div>
              </div>
            )}
          </div>
        </div>

        {/* My expeditions */}
        <div style={S.card}>
          <div style={{ ...S.cardTitle, fontSize: 14 }}>
            Your Expeditions
          </div>
          <div style={{ display: "flex", gap: 16, justifyContent: "center" }}>
            {EXPEDITIONS.map((exp) => (
              <div key={exp} style={{ textAlign: "center" }}>
                <div style={{ fontSize: 10, color: EXP_INFO[exp].hex, marginBottom: 4, fontWeight: 600 }}>
                  {EXP_INFO[exp].icon} {EXP_INFO[exp].name}
                </div>
                <ExpeditionColumn cards={me.expeditions[exp]} expedition={exp} />
              </div>
            ))}
          </div>
        </div>

        {/* Hand + actions */}
        <div style={S.card}>
          <div style={{ ...S.cardTitle, fontSize: 14, display: "flex", justifyContent: "space-between" }}>
            <span>Your Hand</span>
            {selectedCard && phase === "play" && yourTurn && (
              <div style={{ display: "flex", gap: 8 }}>
                {playableAction && (
                  <button style={bs(true)} onClick={handlePlayCard}>
                    Play on {EXP_INFO[selectedCard.expedition].name}
                  </button>
                )}
                {discardableAction && (
                  <button style={bs(false)} onClick={handleDiscardCard}>
                    Discard
                  </button>
                )}
              </div>
            )}
          </div>
          <div style={{ display: "flex", gap: 8, justifyContent: "center", flexWrap: "wrap" }}>
            {sortedHand.map((card) => (
              <Card
                key={card.id}
                card={card}
                selected={selectedCard?.id === card.id}
                onClick={yourTurn && phase === "play" ? () => setSelectedCard(
                  selectedCard?.id === card.id ? null : card
                ) : undefined}
              />
            ))}
          </div>
        </div>

        {/* Error */}
        {game.error && (
          <div style={{ color: "#e74c3c", padding: 12, background: "rgba(231,76,60,0.1)", borderRadius: 6, marginBottom: 12 }}>
            {game.error}
          </div>
        )}

        {/* Game log */}
        <GameLog logs={gameLogs} logRef={logRef} />
      </div>
    </div>
  );
}

// ─── GAME LOG ──────────────────────────────────────────────────────

function GameLog({ logs, logRef }) {
  return (
    <div style={S.card}>
      <div style={{ ...S.cardTitle, fontSize: 14 }}>Game Log</div>
      <div
        ref={logRef}
        style={{
          maxHeight: 160, overflowY: "auto", fontSize: 12, color: "#999",
          lineHeight: 1.6, padding: "0 4px",
        }}
      >
        {logs.length === 0 && <div>No actions yet.</div>}
        {logs.map((msg, i) => (
          <div key={i}>{msg}</div>
        ))}
      </div>
    </div>
  );
}

// ─── APP ───────────────────────────────────────────────────────────

class ErrorBoundary extends React.Component {
  constructor(props) { super(props); this.state = { error: null }; }
  static getDerivedStateFromError(error) { return { error }; }
  render() {
    if (this.state.error) {
      return (
        <div style={{ ...S.app, padding: 40 }}>
          <div style={S.card}>
            <div style={{ color: "#e74c3c", fontSize: 18, marginBottom: 12 }}>GameBoard crashed</div>
            <pre style={{ color: "#ff6b6b", fontSize: 12, whiteSpace: "pre-wrap", wordBreak: "break-all" }}>
              {this.state.error.message}{"\n"}{this.state.error.stack}
            </pre>
            <div style={{ color: "#999", fontSize: 12, marginTop: 12 }}>
              State: <pre style={{ fontSize: 10, maxHeight: 300, overflow: "auto", color: "#888" }}>
                {JSON.stringify(this.props.debugState, null, 2)}
              </pre>
            </div>
          </div>
        </div>
      );
    }
    return this.props.children;
  }
}

export default function App() {
  const game = useGameConnection();

  if (!game.gameStarted) return <Lobby game={game} />;
  if (!game.gameState) return <div style={S.app}><div style={S.content}><div style={S.card}>Waiting for game state...</div></div></div>;
  return (
    <ErrorBoundary debugState={game.gameState}>
      <GameBoard game={game} />
    </ErrorBoundary>
  );
}
