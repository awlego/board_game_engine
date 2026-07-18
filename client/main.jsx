import { createRoot } from "react-dom/client";
import React, { useState, useEffect, useRef, useCallback } from "react";

// ─── Game Imports ──────────────────────────────────────
import BattleLineApp from "./games/BattleLine_MP.jsx";
import ArboretumApp from "./games/Arboretum_MP.jsx";
import LostCitiesApp from "./games/LostCities_MP.jsx";
import DragonApp from "./games/InTheYearOfTheDragon_MP.jsx";
import CaylusApp from "./games/Caylus_MP.jsx";
import TamskApp from "./games/Tamsk_MP.jsx";
import DvonnApp from "./games/Dvonn_MP.jsx";
import YinshApp from "./games/Yinsh_MP.jsx";
import ZertzApp from "./games/Zertz_MP.jsx";
import TzaarApp from "./games/Tzaar_MP.jsx";
import GipfApp from "./games/Gipf_MP.jsx";
import PunctApp from "./games/Punct_MP.jsx";
import LyngkApp from "./games/Lyngk_MP.jsx";
import ShardsApp from "./games/Shards_MP.jsx";
import AgricolaApp, { CREATE_FORM as AGRICOLA_CREATE_FORM } from "./games/Agricola_MP.jsx";

import { WS_URL } from "./ws.js";
import { CreateFormFields, defaultOptions, fetchCardSets, cardSetChoices } from "./games/create_form.jsx";

// Player identity is entered once and remembered across games, tabs, and
// visits. Game clients share this key so their own forms prefill too.
export const NAME_KEY = "bge_player_name";
const loadName = () => localStorage.getItem(NAME_KEY) || "";
const saveName = (n) => localStorage.setItem(NAME_KEY, n);

// ─── Game Registry ─────────────────────────────────────
const GAMES = [
  { id: "gipf",   name: "GIPF",   players: "2",   component: GipfApp,   series: "gipf", desc: "Push pieces from the edge, capture rows of 4" },
  { id: "tamsk",   name: "TAMSK",  players: "2",   component: TamskApp,  series: "gipf", desc: "Race against hourglass timers on a hex board" },
  { id: "zertz",   name: "ZERTZ",  players: "2",   component: ZertzApp,  series: "gipf", desc: "Shrinking board with mandatory marble captures" },
  { id: "dvonn",   name: "DVONN",  players: "2",   component: DvonnApp,  series: "gipf", desc: "Stack pieces, stay connected to DVONN stones" },
  { id: "yinsh",   name: "YINSH",  players: "2",   component: YinshApp,  series: "gipf", desc: "Move rings, flip markers, form rows of 5" },
  { id: "punct",   name: "PUNCT",  players: "2",   component: PunctApp,  series: "gipf", desc: "Connect opposite sides with tri-hex pieces" },
  { id: "tzaar",   name: "TZAAR",  players: "2",   component: TzaarApp,  series: "gipf", desc: "Capture and stack — protect all three types" },
  { id: "lyngk",   name: "LYNGK",  players: "2",   component: LyngkApp,  series: "gipf", desc: "Claim colors, build stacks of 5 unique colors" },
  { id: "battleline", name: "Battle Line",  players: "2",   component: BattleLineApp, series: "other", desc: "Poker-like card formations across 9 flags" },
  { id: "arboretum", name: "Arboretum",    players: "2–4", component: ArboretumApp,  series: "other", desc: "Plant trees in paths, score with careful hand management" },
  { id: "lostcities", name: "Lost Cities",  players: "2",   component: LostCitiesApp, series: "other", desc: "Expedition card game — invest wisely in 5 expeditions" },
  { id: "dragon",  name: "In the Year of the Dragon", players: "2–5", component: DragonApp, series: "other", desc: "Survive disasters in medieval China" },
  { id: "caylus",  name: "Caylus",         players: "2–5", component: CaylusApp,     series: "other", desc: "Build a castle for the king, manage workers" },
  { id: "shards",  name: "Shards of Creation", players: "2–4", component: ShardsApp, series: "other", desc: "Cosmere trick-taking — win tricks, forge shards" },
  { id: "agricola", name: "Agricola", players: "1–4", component: AgricolaApp, series: "other", desc: "Worker-placement farming — grow your family and feed it",
    // Game-specific room options rendered inside the Create Room dialog and
    // passed to the game client via pending_action.options. The spec lives
    // with the game client so its own lobby renders the identical form.
    createForm: AGRICOLA_CREATE_FORM,
    // Extra per-game pages linked from the room browser.
    tools: [{ label: "Draft Set Builder", query: "setbuilder" }],
  },
];

// ─── Styles ────────────────────────────────────────────
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
  content: { position: "relative", zIndex: 1, maxWidth: 1000, margin: "0 auto", padding: "32px 20px" },
  header: { textAlign: "center", marginBottom: 40 },
  title: { fontFamily: font, fontSize: 42, fontWeight: 700, color: "#c9a84c", textShadow: "0 2px 12px rgba(0,0,0,0.6)", margin: 0, letterSpacing: 4 },
  subtitle: { color: "#888", fontSize: 14, marginTop: 8, letterSpacing: 1 },
  sectionTitle: { fontFamily: font, fontSize: 16, color: "#c9a84c", letterSpacing: 2, textTransform: "uppercase", marginBottom: 16, paddingBottom: 8, borderBottom: "1px solid rgba(201,168,76,0.2)" },
  grid: { display: "grid", gridTemplateColumns: "repeat(auto-fill, minmax(280px, 1fr))", gap: 16, marginBottom: 32 },
  card: {
    background: "linear-gradient(135deg, rgba(22,27,34,0.95) 0%, rgba(13,17,23,0.98) 100%)",
    border: "1px solid #30363d", borderRadius: 10, padding: "16px 20px",
    cursor: "pointer", transition: "all 0.25s ease", boxShadow: "0 2px 12px rgba(0,0,0,0.4)",
    display: "flex", flexDirection: "column", gap: 8,
  },
  cardHover: { border: "1px solid #c9a84c", boxShadow: "0 4px 24px rgba(201,168,76,0.15)", transform: "translateY(-2px)" },
  cardName: { fontFamily: font, fontSize: 20, fontWeight: 700, color: "#e8d5a3", letterSpacing: 2, textShadow: "0 1px 6px rgba(0,0,0,0.9)" },
  cardDesc: { fontSize: 12, color: "#aaa", lineHeight: 1.4, flex: 1, textShadow: "0 1px 4px rgba(0,0,0,0.9)" },
  cardMeta: { display: "flex", justifyContent: "space-between", alignItems: "center", marginTop: 4 },
  cardPlayers: { fontSize: 11, color: "#555", padding: "2px 8px", borderRadius: 4, background: "rgba(255,255,255,0.04)", border: "1px solid #30363d" },
  playBtn: { fontFamily: font, fontSize: 11, padding: "4px 14px", borderRadius: 5, border: "1px solid #c9a84c", background: "transparent", color: "#c9a84c", cursor: "pointer", fontWeight: 600, transition: "all 0.2s" },
  backBtn: {
    position: "fixed", top: 12, left: 12, zIndex: 1000,
    fontFamily: font, fontSize: 12, padding: "6px 14px", borderRadius: 6,
    border: "1px solid #30363d", background: "rgba(13,17,23,0.9)",
    color: "#888", cursor: "pointer", transition: "all 0.2s",
    backdropFilter: "blur(8px)",
  },
  btn: { fontFamily: font, fontSize: 14, padding: "8px 20px", borderRadius: 6, border: "1px solid #30363d", background: "linear-gradient(135deg, #21262d 0%, #161b22 100%)", color: "#e8d5a3", cursor: "pointer", fontWeight: 600 },
  btnP: { background: "linear-gradient(135deg, #c9a84c 0%, #a08030 100%)", color: "#0d1117", border: "1px solid #c9a84c" },
  input: { fontFamily: font, fontSize: 14, padding: "8px 12px", borderRadius: 6, border: "1px solid #30363d", background: "rgba(0,0,0,0.3)", color: "#e8d5a3", outline: "none", width: "100%", boxSizing: "border-box" },
  roomCard: {
    background: "rgba(22,27,34,0.9)", border: "1px solid #30363d", borderRadius: 8,
    padding: "10px 14px", marginBottom: 8, display: "flex", justifyContent: "space-between", alignItems: "center",
  },
};

// ─── Game Card ─────────────────────────────────────────
// Box art lives in client/public/boxart/<game.id>.jpg (sourced from BGG).
const boxartUrl = (gameId) => `${import.meta.env.BASE_URL}boxart/${gameId}.jpg`;

function GameCard({ game, onClick }) {
  const [hovered, setHovered] = useState(false);
  const boxart = {
    backgroundImage: `linear-gradient(135deg, rgba(13,17,23,${hovered ? 0.82 : 0.9}) 0%, rgba(13,17,23,${hovered ? 0.55 : 0.72}) 100%), url("${boxartUrl(game.id)}")`,
    backgroundSize: "cover", backgroundPosition: "center",
  };
  return (
    <div style={{ ...S.card, ...(hovered ? S.cardHover : {}), ...boxart }}
      onMouseEnter={() => setHovered(true)} onMouseLeave={() => setHovered(false)} onClick={onClick}>
      <div style={S.cardName}>{game.name}</div>
      <div style={{ ...S.cardDesc, opacity: hovered ? 1 : 0, transition: "opacity 0.25s ease" }}>{game.desc}</div>
      <div style={S.cardMeta}>
        <span style={S.cardPlayers}>{game.players} players</span>
        <span style={{ ...S.playBtn, ...(hovered ? { background: "rgba(201,168,76,0.15)" } : {}) }}>Play</span>
      </div>
    </div>
  );
}

// ─── Room Browser ──────────────────────────────────────
function RoomBrowser({ gameId, gameName, createForm, tools, onJoin, onSpectate, onBack }) {
  const [rooms, setRooms] = useState([]);
  const [loading, setLoading] = useState(true);
  const [name, setName] = useState(loadName);
  const [joinCode, setJoinCode] = useState("");
  const [mode, setMode] = useState(null); // null | "create" | "join_code"
  const [createOpts, setCreateOpts] = useState(() =>
    createForm ? defaultOptions(createForm) : null);
  const [cardSets, setCardSets] = useState([]);

  // Saved card sets feed any choicesFrom: "card_sets" field in the
  // game's create form (today: Agricola's custom draft pools).
  const wantsCardSets = createForm &&
    Object.values(createForm).some((f) => f.choicesFrom === "card_sets");
  useEffect(() => {
    if (!wantsCardSets) return;
    let alive = true;
    fetchCardSets(gameId).then((s) => { if (alive) setCardSets(s); });
    return () => { alive = false; };
  }, [gameId, wantsCardSets]);

  const submit = (roomCode) => {
    const n = name.trim();
    if (!n) return;
    saveName(n);
    onJoin(roomCode, n, mode === "create" ? createOpts : null);
  };

  // Fetch room list
  const fetchRooms = useCallback(() => {
    setLoading(true);
    const ws = new WebSocket(WS_URL);
    ws.onopen = () => { ws.send(JSON.stringify({ type: "list_rooms", game: gameId })); };
    ws.onmessage = (evt) => {
      const msg = JSON.parse(evt.data);
      if (msg.type === "room_list") { setRooms(msg.rooms); setLoading(false); }
      ws.close();
    };
    ws.onerror = () => { setLoading(false); ws.close(); };
  }, [gameId]);

  useEffect(() => { fetchRooms(); }, [fetchRooms]);

  const joinableRooms = rooms.filter(r => r.joinable);
  const spectateRooms = rooms.filter(r => r.started);

  return (
    <div style={S.app}>
      <div style={S.overlay} />
      <div style={S.content}>
        <div style={S.header}>
          <h1 style={{ ...S.title, fontSize: 36, marginBottom: 8 }}>{gameName}</h1>
          <p style={S.subtitle}>Create, join, or watch a game</p>
        </div>

        {/* Action buttons */}
        <div style={{ display: "flex", gap: 12, justifyContent: "center", marginBottom: 24, flexWrap: "wrap" }}>
          <button style={{ ...S.btn, ...S.btnP }} onClick={() => setMode("create")}>Create Room</button>
          <button style={S.btn} onClick={() => setMode("join_code")}>Join by Code</button>
          <button style={{ ...S.btn, borderColor: "#555", color: "#999" }}
            onClick={() => alert("Rules viewer coming soon!")}>Rules</button>
          {(tools || []).map((t) => (
            <button key={t.query} style={{ ...S.btn, borderColor: "#555", color: "#999" }}
              onClick={() => { window.location.search = `?${t.query}`; }}>{t.label}</button>
          ))}
          <button style={{ ...S.btn, color: "#888" }} onClick={onBack}>← Back</button>
        </div>

        {/* Create / Join by Code forms */}
        {mode && (
          <div style={{ ...S.card, maxWidth: 360, margin: "0 auto 24px", cursor: "default" }}>
            <div style={{ fontSize: 16, color: "#c9a84c", marginBottom: 12 }}>
              {mode === "create" ? "Create Room" : "Join by Code"}
            </div>
            <div style={{ display: "flex", flexDirection: "column", gap: 10 }}>
              <input style={S.input} placeholder="Your name" value={name} autoFocus={!name}
                onChange={e => setName(e.target.value)} />
              {mode === "join_code" && (
                <input style={S.input} placeholder="Room code" value={joinCode} autoFocus={!!name}
                  onChange={e => setJoinCode(e.target.value.toUpperCase())} />
              )}
              {mode === "create" && createForm && (
                <div style={{ display: "flex", flexDirection: "column", gap: 10, color: "#aaa" }}>
                  <CreateFormFields form={createForm} value={createOpts}
                    onChange={setCreateOpts}
                    dynamicChoices={{ card_sets: cardSetChoices(cardSets) }}
                    labelStyle={{ color: "#c9a84c", fontWeight: 400 }} />
                </div>
              )}
              <div style={{ display: "flex", gap: 8 }}>
                <button style={S.btn} onClick={() => setMode(null)}>Cancel</button>
                <button style={{ ...S.btn, ...S.btnP }} disabled={!name.trim() || (mode === "join_code" && !joinCode.trim())}
                  onClick={() => submit(mode === "create" ? null : joinCode.trim())}>
                  {mode === "create" ? "Create" : "Join"}
                </button>
              </div>
            </div>
          </div>
        )}

        {/* Open rooms */}
        {joinableRooms.length > 0 && (
          <>
            <div style={S.sectionTitle}>Open Rooms</div>
            {joinableRooms.map(room => (
              <div key={room.room_code} style={S.roomCard}>
                <div>
                  <span style={{ fontWeight: 700, color: "#e8d5a3", fontSize: 14 }}>{room.room_code}</span>
                  <span style={{ color: "#888", fontSize: 12, marginLeft: 12 }}>
                    Host: {room.host_name} · {room.player_count}/{room.max_players} players
                  </span>
                </div>
                <button style={{ ...S.playBtn }} onClick={() => {
                  // One click with a remembered name; otherwise open the join
                  // dialog with the code prefilled so only the name is needed.
                  const n = name.trim();
                  if (n) { saveName(n); onJoin(room.room_code, n, null); }
                  else { setJoinCode(room.room_code); setMode("join_code"); }
                }}>{name.trim() ? `Join as ${name.trim()}` : "Join"}</button>
              </div>
            ))}
          </>
        )}

        {/* Games in progress (spectatable) */}
        {spectateRooms.length > 0 && (
          <>
            <div style={{ ...S.sectionTitle, marginTop: 20 }}>Games in Progress</div>
            {spectateRooms.map(room => (
              <div key={room.room_code} style={S.roomCard}>
                <div>
                  <span style={{ fontWeight: 700, color: "#e8d5a3", fontSize: 14 }}>{room.room_code}</span>
                  <span style={{ color: "#888", fontSize: 12, marginLeft: 12 }}>
                    {room.players.map(p => p.name).join(" vs ")}
                    {room.spectator_count > 0 && ` · ${room.spectator_count} watching`}
                  </span>
                </div>
                <button style={{ ...S.playBtn, borderColor: "#888", color: "#888" }}
                  onClick={() => onSpectate(room.room_code)}>Watch</button>
              </div>
            ))}
          </>
        )}

        {loading && <div style={{ textAlign: "center", color: "#555", marginTop: 20 }}>Loading rooms...</div>}
        {!loading && rooms.length === 0 && (
          <div style={{ textAlign: "center", color: "#555", marginTop: 20 }}>No active rooms. Create one to start!</div>
        )}

        <div style={{ textAlign: "center", marginTop: 16 }}>
          <button style={{ ...S.btn, fontSize: 11, padding: "4px 12px", color: "#555", borderColor: "#333" }} onClick={fetchRooms}>
            Refresh
          </button>
        </div>
      </div>
    </div>
  );
}


// ─── Stats ─────────────────────────────────────────────
const gameDisplayName = (id) => (GAMES.find(g => g.id === id) || { name: id }).name;
const pct = (wins, plays) => plays ? `${Math.round((wins / plays) * 100)}%` : "—";

const T = {
  table: { width: "100%", borderCollapse: "collapse", fontSize: 13, marginBottom: 28 },
  th: { textAlign: "left", color: "#888", fontWeight: 400, fontSize: 11, letterSpacing: 1, textTransform: "uppercase", padding: "6px 10px", borderBottom: "1px solid #30363d" },
  td: { padding: "7px 10px", borderBottom: "1px solid rgba(48,54,61,0.5)", color: "#ccc" },
  num: { textAlign: "right" },
};

function StatsPage({ onBack }) {
  const [stats, setStats] = useState(null);   // null = loading
  const [error, setError] = useState(false);

  useEffect(() => {
    const ws = new WebSocket(WS_URL);
    ws.onopen = () => { ws.send(JSON.stringify({ type: "stats" })); };
    ws.onmessage = (evt) => {
      const msg = JSON.parse(evt.data);
      if (msg.type === "stats") setStats(msg);
      else setError(true);
      ws.close();
    };
    ws.onerror = () => { setError(true); ws.close(); };
    return () => ws.close();
  }, []);

  const empty = stats && !stats.games.length;
  return (
    <div style={S.app}>
      <div style={S.overlay} />
      <div style={S.content}>
        <div style={S.header}>
          <h1 style={{ ...S.title, fontSize: 36, marginBottom: 8 }}>Hall of Records</h1>
          <p style={S.subtitle}>Every game played, every victory claimed</p>
        </div>
        <div style={{ display: "flex", justifyContent: "center", marginBottom: 24 }}>
          <button style={{ ...S.btn, color: "#888" }} onClick={onBack}>← Back</button>
        </div>

        {!stats && !error && <div style={{ textAlign: "center", color: "#555" }}>Consulting the archives...</div>}
        {error && <div style={{ textAlign: "center", color: "#a05555" }}>Stats are unavailable right now.</div>}
        {empty && <div style={{ textAlign: "center", color: "#555" }}>No completed games yet — play something!</div>}

        {stats && !empty && (
          <>
            <div style={S.sectionTitle}>Player Records</div>
            <table style={T.table}>
              <thead><tr>
                <th style={T.th}>Player</th><th style={T.th}>Game</th>
                <th style={{ ...T.th, ...T.num }}>Plays</th>
                <th style={{ ...T.th, ...T.num }}>Wins</th>
                <th style={{ ...T.th, ...T.num }}>Win rate</th>
                <th style={{ ...T.th, ...T.num }}>Avg score</th>
                <th style={{ ...T.th, ...T.num }}>Best</th>
              </tr></thead>
              <tbody>
                {stats.players.map((p, i) => (
                  <tr key={i}>
                    <td style={{ ...T.td, color: "#e8d5a3", fontWeight: 600 }}>{p.who}</td>
                    <td style={T.td}>{gameDisplayName(p.game_name)}</td>
                    <td style={{ ...T.td, ...T.num }}>{p.plays}</td>
                    <td style={{ ...T.td, ...T.num }}>{p.wins}</td>
                    <td style={{ ...T.td, ...T.num, color: "#c9a84c" }}>{pct(p.wins, p.plays)}</td>
                    <td style={{ ...T.td, ...T.num }}>{p.avg_score ?? "—"}</td>
                    <td style={{ ...T.td, ...T.num }}>{p.best_score ?? "—"}</td>
                  </tr>
                ))}
              </tbody>
            </table>

            <div style={S.sectionTitle}>Games</div>
            <table style={T.table}>
              <thead><tr>
                <th style={T.th}>Game</th>
                <th style={{ ...T.th, ...T.num }}>Started</th>
                <th style={{ ...T.th, ...T.num }}>Finished</th>
                <th style={{ ...T.th, ...T.num }}>Abandoned</th>
                <th style={{ ...T.th, ...T.num }}>Last played</th>
              </tr></thead>
              <tbody>
                {stats.games.map((g) => (
                  <tr key={g.game_name}>
                    <td style={{ ...T.td, color: "#e8d5a3" }}>{gameDisplayName(g.game_name)}</td>
                    <td style={{ ...T.td, ...T.num }}>{g.starts}</td>
                    <td style={{ ...T.td, ...T.num }}>{g.finished}</td>
                    <td style={{ ...T.td, ...T.num }}>{g.abandoned}</td>
                    <td style={{ ...T.td, ...T.num }}>{g.last_played ? new Date(g.last_played).toLocaleDateString() : "—"}</td>
                  </tr>
                ))}
              </tbody>
            </table>

            {stats.recent.length > 0 && (
              <>
                <div style={S.sectionTitle}>Recent Games</div>
                {stats.recent.map((g, i) => (
                  <div key={i} style={S.roomCard}>
                    <div>
                      <span style={{ fontWeight: 700, color: "#e8d5a3", fontSize: 14 }}>{gameDisplayName(g.game_name)}</span>
                      <span style={{ color: "#888", fontSize: 12, marginLeft: 12 }}>
                        {g.players.map((p, j) => (
                          <span key={j}>
                            {j > 0 && " · "}
                            <span style={p.is_winner ? { color: "#c9a84c", fontWeight: 700 } : {}}>
                              {p.is_winner ? "♛ " : ""}{p.username || p.name}{p.score != null ? ` (${p.score})` : ""}
                            </span>
                          </span>
                        ))}
                      </span>
                    </div>
                    <span style={{ color: "#555", fontSize: 12 }}>
                      {g.finished_at ? new Date(g.finished_at).toLocaleDateString() : ""}
                    </span>
                  </div>
                ))}
              </>
            )}
          </>
        )}
      </div>
    </div>
  );
}

// ─── Game Selector ─────────────────────────────────────
function GameSelector({ onSelect, onStats }) {
  const gipfGames = GAMES.filter(g => g.series === "gipf");
  const otherGames = GAMES.filter(g => g.series === "other");

  return (
    <div style={S.app}>
      <div style={S.overlay} />
      <div style={S.content}>
        <div style={S.header}>
          <h1 style={S.title}>Board Game Engine</h1>
          <p style={S.subtitle}>Choose a game to play</p>
          <button style={{ ...S.btn, fontSize: 12, marginTop: 12, color: "#c9a84c", borderColor: "rgba(201,168,76,0.4)" }}
            onClick={onStats}>♛ Hall of Records</button>
        </div>

        <div style={S.sectionTitle}>The GIPF Project</div>
        <div style={S.grid}>
          {gipfGames.map(game => <GameCard key={game.id} game={game} onClick={() => onSelect(game.id)} />)}
        </div>

        <div style={S.sectionTitle}>Classic Games</div>
        <div style={S.grid}>
          {otherGames.map(game => <GameCard key={game.id} game={game} onClick={() => onSelect(game.id)} />)}
        </div>
      </div>
    </div>
  );
}

// ─── Main App ──────────────────────────────────────────
function MainApp() {
  const [selectedGame, setSelectedGame] = useState(null);
  const [gameMode, setGameMode] = useState(null); // null | "browse" | "playing" | "spectating"
  const [joinInfo, setJoinInfo] = useState(null); // { roomCode, playerName }
  const [showStats, setShowStats] = useState(false);

  const handleBack = useCallback(() => {
    sessionStorage.removeItem("game_token");
    sessionStorage.removeItem("pending_action");
    sessionStorage.removeItem("pending_spectate");
    setSelectedGame(null);
    setGameMode(null);
    setJoinInfo(null);
  }, []);

  if (showStats) {
    return <StatsPage onBack={() => setShowStats(false)} />;
  }

  // Game selector
  if (!selectedGame) {
    return <GameSelector onSelect={(id) => { setSelectedGame(id); setGameMode("browse"); }}
      onStats={() => setShowStats(true)} />;
  }

  const game = GAMES.find(g => g.id === selectedGame);
  if (!game) { handleBack(); return null; }

  // Room browser (pre-lobby)
  if (gameMode === "browse") {
    return (
      <RoomBrowser
        gameId={game.id}
        gameName={game.name}
        createForm={game.createForm}
        tools={game.tools}
        onJoin={(roomCode, playerName, options) => {
          // Store intent for the game hook to auto-create/join on mount
          sessionStorage.setItem("pending_action", JSON.stringify({
            gameId: game.id, roomCode, playerName,
            ...(options ? { options } : {}),
          }));
          sessionStorage.removeItem("game_token");
          setJoinInfo({ roomCode, playerName });
          setGameMode("playing");
        }}
        onSpectate={(roomCode) => {
          sessionStorage.setItem("pending_spectate", JSON.stringify({
            gameId: game.id, roomCode,
          }));
          setGameMode("spectating");
        }}
        onBack={handleBack}
      />
    );
  }

  // Playing or spectating — mount the game component directly.
  // The game hook reads pending_action from sessionStorage to auto-create/join.
  const GameComponent = game.component;
  return (
    <div>
      <button style={S.backBtn} onClick={handleBack}
        onMouseEnter={e => { e.target.style.color = "#c9a84c"; e.target.style.borderColor = "#c9a84c"; }}
        onMouseLeave={e => { e.target.style.color = "#888"; e.target.style.borderColor = "#30363d"; }}>
        ← Games
      </button>
      <GameComponent />
    </div>
  );
}

// Standalone pages: /?cardlab renders the Agricola card gallery,
// /?setbuilder the Agricola draft set builder, instead of the app.
const CardLabLazy = React.lazy(() => import("./games/agricola_cardlab.jsx"));
const SetBuilderLazy = React.lazy(() => import("./games/agricola_set_builder.jsx"));
const params = new URLSearchParams(window.location.search);
const root = params.has("cardlab")
  ? <React.Suspense fallback={null}><CardLabLazy /></React.Suspense>
  : params.has("setbuilder")
    ? <React.Suspense fallback={null}><SetBuilderLazy /></React.Suspense>
    : <MainApp />;

createRoot(document.getElementById("root")).render(root);
