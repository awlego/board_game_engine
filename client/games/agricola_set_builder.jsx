import { useEffect, useMemo, useRef, useState } from "react";

import CARD_CATALOG from "./agricola_cards.json";
import { AgricolaCard } from "./agricola_card.jsx";
import { WS_URL } from "../ws.js";

// ============================================================
// Draft Set Builder — curate a named card pool (occupations +
// minor improvements) that rooms can deal or draft from instead
// of whole decks. Mounted by main.jsx at /?setbuilder; sets are
// saved on the game server (list_card_sets / save_card_set /
// delete_card_set, see client/PROTOCOL.md) and offered in both
// create-room dialogs' "Card pool" select.
// ============================================================

const GAME = "agricola";
const PAGE = 60;

// Majors are the fixed supply board, never dealt or drafted.
const POOL = Object.entries(CARD_CATALOG)
  .filter(([, s]) => s.type === "occupation" || s.type === "minor");
const BY_ID = Object.fromEntries(POOL);
const DECKS = [...new Set(POOL.map(([, s]) => s.deck))].sort();

// Quick "mentions" filter: common goods/mechanics a designer hunts
// for when balancing a pool (matched against rules text + name).
const MENTIONS = [
  "wood", "clay", "reed", "stone", "food", "grain", "vegetable",
  "sheep", "boar", "cattle", "animal", "field", "plow", "sow", "bake",
  "oven", "fireplace", "fence", "pasture", "stable", "room", "renovate",
  "family growth", "starting player", "harvest", "begging",
];

const SORTS = {
  id: { label: "Card id", fn: (a, b) => a[0].localeCompare(b[0]) },
  name: { label: "Name", fn: (a, b) => (a[1].name || "").localeCompare(b[1].name || "") },
  deck: { label: "Deck", fn: (a, b) => (a[1].deck || "").localeCompare(b[1].deck || "") || a[0].localeCompare(b[0]) },
  points: { label: "Points", fn: (a, b) => (b[1].points || 0) - (a[1].points || 0) },
};

// One-shot request/response over a fresh socket (same pattern as the
// lobby's room list): resolves on `okType`, rejects on server error.
function wsCall(payload, okType) {
  return new Promise((resolve, reject) => {
    let ws;
    try { ws = new WebSocket(WS_URL); } catch (e) { reject(e); return; }
    const fail = (message) => { reject(new Error(message)); ws.close(); };
    ws.onopen = () => ws.send(JSON.stringify(payload));
    ws.onmessage = (evt) => {
      const msg = JSON.parse(evt.data);
      if (msg.type === okType) { resolve(msg); ws.close(); }
      else if (msg.type === "error") fail(msg.message || "Server error");
    };
    ws.onerror = () => fail("Could not reach the game server");
  });
}

const download = (filename, data) => {
  const url = URL.createObjectURL(
    new Blob([JSON.stringify(data, null, 1)], { type: "application/json" }));
  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  a.click();
  URL.revokeObjectURL(url);
};

// ── Styles (CardLab's dark stone theme) ─────────────────────────────

const S = {
  page: { minHeight: "100vh", background: "#292524", fontFamily: "system-ui, sans-serif", color: "#e7e5e4" },
  bar: {
    display: "flex", gap: 8, alignItems: "center", flexWrap: "wrap",
    padding: "8px 14px", background: "#1c1917", position: "sticky",
    top: 0, zIndex: 3, borderBottom: "1px solid #44403c",
  },
  input: {
    background: "#292524", color: "#e7e5e4", border: "1px solid #57534e",
    borderRadius: 6, padding: "5px 9px", fontSize: 13, fontFamily: "inherit",
  },
  btn: {
    background: "#44403c", color: "#e7e5e4", border: "1px solid #57534e",
    borderRadius: 6, padding: "5px 10px", fontSize: 12, cursor: "pointer",
    fontFamily: "inherit",
  },
  chip: (on) => ({
    background: on ? "#a3e635" : "#292524", color: on ? "#1a2e05" : "#a8a29e",
    border: `1px solid ${on ? "#a3e635" : "#57534e"}`, borderRadius: 12,
    padding: "2px 9px", fontSize: 11, cursor: "pointer", fontWeight: on ? 700 : 400,
  }),
  side: {
    width: 290, flexShrink: 0, padding: 14, display: "flex",
    flexDirection: "column", gap: 12, position: "sticky", top: 49,
    maxHeight: "calc(100vh - 49px)", overflowY: "auto", boxSizing: "border-box",
  },
  panel: { background: "#1c1917", border: "1px solid #44403c", borderRadius: 8, padding: 12 },
  h: { fontSize: 12, fontWeight: 700, color: "#fbbf24", marginBottom: 8, letterSpacing: 0.5 },
  small: { fontSize: 11, color: "#a8a29e" },
};

const label = (text) => (
  <span style={{ fontSize: 11, color: "#a8a29e", marginLeft: 2 }}>{text}</span>
);

// ── Filter / sidebar helpers ─────────────────────────────────────────

function matches(cid, s, f) {
  if (f.type && s.type !== f.type) return false;
  if (f.decks.length && !f.decks.includes(s.deck)) return false;
  if (f.playableOnly && s.implemented === false) return false;
  if (f.players && (s.min_players || 1) > f.players) return false;
  if (f.membership === "in" && !f.inSet.has(cid)) return false;
  if (f.membership === "out" && f.inSet.has(cid)) return false;
  if (f.pointsOnly && !(s.points > 0)) return false;
  const hay = `${cid} ${s.name || ""} ${s.text || ""}`.toLowerCase();
  if (f.mention && !hay.includes(f.mention)) return false;
  const q = f.query.trim().toLowerCase();
  if (q && !hay.includes(q)) return false;
  return true;
}

// Deal/draft feasibility at each player count: cards under a player's
// minimum don't get dealt (min_players), and every player needs a
// full packet of each stage. Mirrors server cards.deal_hands.
function feasibility(cardIds) {
  const rows = [];
  for (let n = 1; n <= 4; n++) {
    let occ = 0, minor = 0;
    for (const cid of cardIds) {
      const s = BY_ID[cid];
      if (!s || (s.min_players || 1) > n) continue;
      if (s.type === "occupation") occ++;
      else minor++;
    }
    rows.push({ n, occ, minor, packet: Math.min(14, Math.floor(occ / n), Math.floor(minor / n)) });
  }
  return rows;
}

function SetStats({ cardIds }) {
  const rows = feasibility(cardIds);
  const perDeck = {};
  for (const cid of cardIds) {
    const d = BY_ID[cid]?.deck || "?";
    perDeck[d] = (perDeck[d] || 0) + 1;
  }
  return (
    <>
      <table style={{ width: "100%", fontSize: 11, borderCollapse: "collapse", color: "#d6d3d1" }}>
        <thead>
          <tr style={{ color: "#a8a29e", textAlign: "right" }}>
            <th style={{ textAlign: "left", fontWeight: 400 }}>Players</th>
            <th style={{ fontWeight: 400 }}>Occ</th>
            <th style={{ fontWeight: 400 }}>Minor</th>
            <th style={{ fontWeight: 400 }}>Max deal</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((r) => (
            <tr key={r.n} style={{ textAlign: "right" }}>
              <td style={{ textAlign: "left", padding: "2px 0" }}>{r.n}</td>
              <td>{r.occ}</td>
              <td>{r.minor}</td>
              <td style={{ color: r.packet >= 7 ? "#a3e635" : r.packet >= 1 ? "#fbbf24" : "#f87171", fontWeight: 700 }}>
                {r.packet}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
      <div style={{ ...S.small, marginTop: 6 }}>
        “Max deal” = biggest hand/packet per player this pool supports
        (the standard game deals 7+7). Cards marked 3+/4+ only count at
        those player counts.
      </div>
      {Object.keys(perDeck).length > 0 && (
        <div style={{ ...S.small, marginTop: 6 }}>
          By deck: {Object.entries(perDeck).sort()
            .map(([d, c]) => `${d}:${c}`).join("  ")}
        </div>
      )}
    </>
  );
}

// ── Page ─────────────────────────────────────────────────────────────

export default function SetBuilder() {
  // Filters
  const [query, setQuery] = useState("");
  const [type, setType] = useState("");
  const [decks, setDecks] = useState([]);
  const [players, setPlayers] = useState(0);
  const [playableOnly, setPlayableOnly] = useState(true);
  const [membership, setMembership] = useState("all");
  const [pointsOnly, setPointsOnly] = useState(false);
  const [mention, setMention] = useState("");
  const [sort, setSort] = useState("id");
  const [size, setSize] = useState(210);
  const [limit, setLimit] = useState(PAGE);

  // Current set + saved sets
  const [setId, setSetId] = useState(null);
  const [setName, setSetName] = useState("");
  const [inSet, setInSet] = useState(() => new Set());
  const [savedSets, setSavedSets] = useState([]);
  const [status, setStatus] = useState(null); // {ok, text}
  const [confirmDelete, setConfirmDelete] = useState(null);
  const fileRef = useRef(null);

  const say = (ok, text) => setStatus({ ok, text });

  const refreshSets = () =>
    wsCall({ type: "list_card_sets", game: GAME }, "card_set_list")
      .then((msg) => setSavedSets(msg.sets || []))
      .catch((e) => say(false, e.message));
  useEffect(() => { refreshSets(); }, []);

  const filters = { query, type, decks, players: +players, playableOnly, membership, pointsOnly, mention, inSet };
  const shown = useMemo(
    () => POOL.filter(([cid, s]) => matches(cid, s, filters)).sort(SORTS[sort].fn),
    [query, type, decks, players, playableOnly, membership, pointsOnly, mention, sort, inSet],
  );

  const resetPage = () => setLimit(PAGE);

  const toggle = (cid) => {
    if (BY_ID[cid]?.implemented === false) return;
    const next = new Set(inSet);
    next.has(cid) ? next.delete(cid) : next.add(cid);
    setInSet(next);
  };

  const bulk = (add) => {
    const next = new Set(inSet);
    for (const [cid, s] of shown) {
      if (s.implemented === false) continue;
      add ? next.add(cid) : next.delete(cid);
    }
    setInSet(next);
  };

  // Cards that ended up in the set but aren't playable (usually from
  // an imported file); the server refuses to save them.
  const invalid = [...inSet].filter((cid) => !BY_ID[cid] || BY_ID[cid].implemented === false);

  const save = (asNew) => {
    const payload = {
      name: setName.trim(), cards: [...inSet].sort(),
      author: localStorage.getItem("bge_player_name") || undefined,
    };
    if (!asNew && setId) payload.id = setId;
    wsCall({ type: "save_card_set", game: GAME, set: payload }, "card_set_saved")
      .then((msg) => {
        setSetId(msg.set.id);
        say(true, `Saved “${msg.set.name}” (${msg.set.cards.length} cards)`);
        refreshSets();
      })
      .catch((e) => say(false, e.message));
  };

  const load = (s) => {
    setSetId(s.id);
    setSetName(s.name);
    setInSet(new Set(s.cards || []));
    setStatus(null);
    setConfirmDelete(null);
  };

  const remove = (s) => {
    wsCall({ type: "delete_card_set", game: GAME, id: s.id }, "card_set_deleted")
      .then(() => {
        if (setId === s.id) setSetId(null);
        say(true, `Deleted “${s.name}”`);
        setConfirmDelete(null);
        refreshSets();
      })
      .catch((e) => say(false, e.message));
  };

  const importFile = (file) => {
    file.text().then((text) => {
      const data = JSON.parse(text);
      if (!Array.isArray(data.cards)) throw new Error("no cards[] in file");
      setSetId(null);
      setSetName(data.name || file.name.replace(/\.json$/, ""));
      setInSet(new Set(data.cards.filter((c) => typeof c === "string")));
      say(true, `Imported ${data.cards.length} cards — review and save`);
    }).catch((e) => say(false, `Import failed: ${e.message}`));
  };

  const occCount = [...inSet].filter((c) => BY_ID[c]?.type === "occupation").length;
  const minorCount = [...inSet].filter((c) => BY_ID[c]?.type === "minor").length;

  return (
    <div style={S.page}>
      {/* Filter bar */}
      <div style={S.bar}>
        <b style={{ color: "#fbbf24", fontSize: 15, marginRight: 4 }}>🚜 Draft Set Builder</b>
        <input style={{ ...S.input, width: 190 }} placeholder="Search name / id / text…"
          value={query} onChange={(e) => { setQuery(e.target.value); resetPage(); }} />
        <select style={S.input} value={type} onChange={(e) => { setType(e.target.value); resetPage(); }}>
          <option value="">Occupations + minors</option>
          <option value="occupation">Occupations</option>
          <option value="minor">Minor improvements</option>
        </select>
        <select style={S.input} value={mention} onChange={(e) => { setMention(e.target.value); resetPage(); }}>
          <option value="">Mentions…</option>
          {MENTIONS.map((m) => <option key={m} value={m}>{m}</option>)}
        </select>
        <select style={S.input} value={players} onChange={(e) => { setPlayers(e.target.value); resetPage(); }}>
          <option value={0}>Any player count</option>
          {[1, 2, 3, 4].map((n) => <option key={n} value={n}>Usable with {n}p</option>)}
        </select>
        <select style={S.input} value={membership} onChange={(e) => { setMembership(e.target.value); resetPage(); }}>
          <option value="all">In + out of set</option>
          <option value="in">In set only</option>
          <option value="out">Not in set</option>
        </select>
        <label style={{ ...S.small, cursor: "pointer" }}>
          <input type="checkbox" checked={playableOnly}
            onChange={(e) => { setPlayableOnly(e.target.checked); resetPage(); }} /> playable only
        </label>
        <label style={{ ...S.small, cursor: "pointer" }}>
          <input type="checkbox" checked={pointsOnly}
            onChange={(e) => { setPointsOnly(e.target.checked); resetPage(); }} /> bonus points
        </label>
        <select style={S.input} value={sort} onChange={(e) => setSort(e.target.value)}>
          {Object.entries(SORTS).map(([k, v]) => <option key={k} value={k}>Sort: {v.label}</option>)}
        </select>
        <label style={S.small}>
          size <input type="range" min={130} max={340} value={size}
            onChange={(e) => setSize(+e.target.value)} style={{ verticalAlign: "middle", width: 70 }} />
        </label>
      </div>

      {/* Deck chips + bulk row */}
      <div style={{ ...S.bar, top: 49, zIndex: 2, background: "#241f1c" }}>
        {label("Decks:")}
        {DECKS.map((d) => (
          <span key={d} style={S.chip(decks.includes(d))}
            onClick={() => {
              setDecks(decks.includes(d) ? decks.filter((x) => x !== d) : [...decks, d]);
              resetPage();
            }}>{d}</span>
        ))}
        {decks.length > 0 && (
          <span style={{ ...S.chip(false), borderStyle: "dashed" }}
            onClick={() => { setDecks([]); resetPage(); }}>clear</span>
        )}
        <span style={{ marginLeft: "auto", ...S.small }}>{shown.length} shown</span>
        <button style={S.btn} onClick={() => bulk(true)}>+ Add all shown</button>
        <button style={S.btn} onClick={() => bulk(false)}>− Remove all shown</button>
      </div>

      <div style={{ display: "flex", alignItems: "flex-start" }}>
        {/* Card grid */}
        <div style={{ flex: 1, minWidth: 0, padding: 14 }}>
          <div style={{ display: "flex", flexWrap: "wrap", gap: 12, alignItems: "flex-start" }}>
            {shown.slice(0, limit).map(([cid, spec]) => (
              <div key={cid} style={{ position: "relative" }}>
                <AgricolaCard cid={cid} spec={spec} width={size}
                  selected={inSet.has(cid)}
                  playable={spec.implemented === false ? false : undefined}
                  onClick={() => toggle(cid)} />
                {inSet.has(cid) && (
                  <span style={{
                    position: "absolute", top: 6, right: 6, background: "#a3e635",
                    color: "#1a2e05", borderRadius: "50%", width: 22, height: 22,
                    display: "flex", alignItems: "center", justifyContent: "center",
                    fontSize: 14, fontWeight: 700, pointerEvents: "none",
                    boxShadow: "0 1px 4px rgba(0,0,0,0.5)",
                  }}>✓</span>
                )}
                {spec.implemented === false && (
                  <span title={spec.reason} style={{
                    position: "absolute", left: 6, bottom: 6, right: 6,
                    background: "rgba(0,0,0,0.75)", color: "#fbbf24",
                    fontSize: 10, borderRadius: 4, padding: "2px 5px",
                    pointerEvents: "none", textAlign: "center",
                  }}>not playable yet</span>
                )}
              </div>
            ))}
          </div>
          {shown.length > limit && (
            <button style={{ ...S.btn, marginTop: 14 }} onClick={() => setLimit(limit + PAGE)}>
              Show {Math.min(PAGE, shown.length - limit)} more
            </button>
          )}
          {shown.length === 0 && (
            <div style={{ ...S.small, padding: 20 }}>No cards match these filters.</div>
          )}
        </div>

        {/* Sidebar */}
        <div style={S.side}>
          <div style={S.panel}>
            <div style={S.h}>Current set{setId ? "" : " (unsaved)"}</div>
            <input style={{ ...S.input, width: "100%", boxSizing: "border-box", marginBottom: 8 }}
              placeholder="Set name" value={setName}
              onChange={(e) => setSetName(e.target.value)} />
            <div style={{ fontSize: 13, marginBottom: 6 }}>
              <b style={{ color: "#a3e635" }}>{occCount}</b> occupations · {" "}
              <b style={{ color: "#a3e635" }}>{minorCount}</b> minors
            </div>
            {invalid.length > 0 && (
              <div style={{ fontSize: 11, color: "#f87171", marginBottom: 6 }}>
                {invalid.length} card(s) aren’t playable and will block saving.{" "}
                <a href="#" style={{ color: "#fbbf24" }} onClick={(e) => {
                  e.preventDefault();
                  setInSet(new Set([...inSet].filter((c) => !invalid.includes(c))));
                }}>Remove them</a>
              </div>
            )}
            <SetStats cardIds={[...inSet]} />
            <div style={{ display: "flex", gap: 6, flexWrap: "wrap", marginTop: 10 }}>
              <button style={{ ...S.btn, background: "#4d7c0f", borderColor: "#65a30d" }}
                disabled={!setName.trim() || inSet.size === 0}
                onClick={() => save(false)}>
                {setId ? "Save changes" : "Save"}
              </button>
              {setId && (
                <button style={S.btn} disabled={!setName.trim() || inSet.size === 0}
                  onClick={() => save(true)}>Save as copy</button>
              )}
              <button style={S.btn} onClick={() => {
                setSetId(null); setSetName(""); setInSet(new Set()); setStatus(null);
              }}>New</button>
              <button style={S.btn} disabled={inSet.size === 0}
                onClick={() => download(`${setName.trim() || "card-set"}.json`,
                  { name: setName.trim(), cards: [...inSet].sort() })}>
                Export
              </button>
              <button style={S.btn} onClick={() => fileRef.current?.click()}>Import</button>
              <input ref={fileRef} type="file" accept=".json" style={{ display: "none" }}
                onChange={(e) => {
                  if (e.target.files?.[0]) importFile(e.target.files[0]);
                  e.target.value = "";
                }} />
            </div>
            {status && (
              <div style={{ fontSize: 11, marginTop: 8, color: status.ok ? "#a3e635" : "#f87171" }}>
                {status.text}
              </div>
            )}
          </div>

          <div style={S.panel}>
            <div style={S.h}>Saved sets</div>
            {savedSets.length === 0 && <div style={S.small}>None yet — build one and save it.</div>}
            {savedSets.map((s) => (
              <div key={s.id} style={{
                display: "flex", alignItems: "center", gap: 6, padding: "4px 0",
                borderBottom: "1px solid #292524", fontSize: 12,
              }}>
                <div style={{ flex: 1, minWidth: 0 }}>
                  <div style={{
                    overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap",
                    fontWeight: s.id === setId ? 700 : 400,
                    color: s.id === setId ? "#a3e635" : "#e7e5e4",
                  }}>{s.name}</div>
                  <div style={S.small}>
                    {(s.cards || []).length} cards{s.author ? ` · ${s.author}` : ""}
                  </div>
                </div>
                <button style={{ ...S.btn, padding: "2px 8px", fontSize: 11 }}
                  onClick={() => load(s)}>Load</button>
                <button style={{
                  ...S.btn, padding: "2px 8px", fontSize: 11,
                  ...(confirmDelete === s.id ? { background: "#b91c1c", borderColor: "#ef4444" } : {}),
                }}
                  onClick={() => confirmDelete === s.id ? remove(s) : setConfirmDelete(s.id)}
                  onMouseLeave={() => confirmDelete === s.id && setConfirmDelete(null)}>
                  {confirmDelete === s.id ? "Sure?" : "✕"}
                </button>
              </div>
            ))}
          </div>

          <div style={{ ...S.small, lineHeight: 1.5 }}>
            Click a card to add or remove it. Saved sets appear as “Card
            pool” choices when creating an Agricola room — the deal
            and the pick-and-pass draft then use exactly these cards.
            {" "}<a href={import.meta.env.BASE_URL} style={{ color: "#fbbf24" }}>← Back to games</a>
          </div>
        </div>
      </div>
    </div>
  );
}
