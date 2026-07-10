import { useMemo, useState } from "react";

import CARD_CATALOG from "./agricola_cards.json";
import { AgricolaCard, CARD_THEMES } from "./agricola_card.jsx";

// ============================================================
// CardLab — dev gallery for the Agricola card renderer.
// Mounted by main.jsx when the URL contains ?cardlab.
// Browse/filter the full catalog at any render size.
// ============================================================

const ALL = Object.entries(CARD_CATALOG);
const DECKS = [...new Set(ALL.map(([, s]) => s.deck))].sort();
const PAGE = 48;

const bar = {
  display: "flex", gap: 10, alignItems: "center", flexWrap: "wrap",
  padding: "10px 14px", background: "#1c1917", position: "sticky",
  top: 0, zIndex: 2, borderBottom: "1px solid #44403c",
};
const input = {
  background: "#292524", color: "#e7e5e4", border: "1px solid #57534e",
  borderRadius: 6, padding: "5px 9px", fontSize: 13,
};

export default function CardLab() {
  const [query, setQuery] = useState("");
  const [deck, setDeck] = useState("base");
  const [type, setType] = useState("");
  const [size, setSize] = useState(230);
  const [limit, setLimit] = useState(PAGE);

  const cards = useMemo(() => {
    const q = query.trim().toLowerCase();
    return ALL.filter(([cid, s]) =>
      (!deck || s.deck === deck) &&
      (!type || s.type === type) &&
      (!q || cid.toLowerCase().includes(q) ||
        (s.name || "").toLowerCase().includes(q) ||
        (s.text || "").toLowerCase().includes(q)));
  }, [query, deck, type]);

  return (
    <div style={{ minHeight: "100vh", background: "#292524", fontFamily: "system-ui, sans-serif" }}>
      <div style={bar}>
        <b style={{ color: "#fbbf24", fontSize: 15 }}>Agricola CardLab</b>
        <input style={{ ...input, width: 220 }} placeholder="Search name / id / text…"
          value={query} onChange={(e) => { setQuery(e.target.value); setLimit(PAGE); }} />
        <select style={input} value={deck} onChange={(e) => { setDeck(e.target.value); setLimit(PAGE); }}>
          <option value="">All decks</option>
          {DECKS.map((d) => <option key={d} value={d}>{d}</option>)}
        </select>
        <select style={input} value={type} onChange={(e) => { setType(e.target.value); setLimit(PAGE); }}>
          <option value="">All types</option>
          {Object.keys(CARD_THEMES).map((t) => <option key={t} value={t}>{t}</option>)}
        </select>
        <label style={{ color: "#a8a29e", fontSize: 12 }}>
          size <input type="range" min={120} max={420} value={size}
            onChange={(e) => setSize(+e.target.value)} style={{ verticalAlign: "middle" }} />
        </label>
        <span style={{ color: "#a8a29e", fontSize: 12 }}>{cards.length} cards</span>
      </div>

      <div style={{ display: "flex", flexWrap: "wrap", gap: 16, padding: 16, alignItems: "flex-start" }}>
        {cards.slice(0, limit).map(([cid, spec]) => (
          <AgricolaCard key={cid} cid={cid} spec={spec} width={size} />
        ))}
      </div>

      {cards.length > limit && (
        <div style={{ padding: "0 16px 24px" }}>
          <button style={{ ...input, cursor: "pointer" }} onClick={() => setLimit(limit + PAGE)}>
            Show {Math.min(PAGE, cards.length - limit)} more
          </button>
        </div>
      )}
    </div>
  );
}
