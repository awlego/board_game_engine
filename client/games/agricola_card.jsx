import { useId, useState } from "react";

// ============================================================
// Agricola card renderer — official-style card frames composed
// entirely from a catalog spec (agricola_cards.json entry) plus
// an optional source art image.
//
// Everything scales from the `width` prop: the card root sets
// fontSize = width / 25, and all internal dimensions are in em
// (so 1em == 10px on a 250px-wide card, the design size).
//
// Composable pieces (all exported): CardFrame, TitlePlate,
// ArtWindow, CostChips, VPBadge, MinPlayersBadge, DeckBadge,
// PrereqPlate, PassMark, ActionMark, CardText.
// <AgricolaCard> assembles them in the official layout.
// ============================================================

// Icons available to card text via {token} markup and cost chips.
// Mirrors the GOODS map in Agricola_MP.jsx.
export const ICONS = {
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
  family:    { icon: "👤", label: "Family member", color: "#1d4ed8" },
  room:      { icon: "🚪", label: "Room",      color: "#78350f" },
  field:     { icon: "🟤", label: "Field",     color: "#a16207" },
  fence:     { icon: "🚧", label: "Fence",     color: "#57534e" },
  stable:    { icon: "🐴", label: "Stable",    color: "#7c2d12" },
};

// Frame palettes sampled from official-style card scans.
export const CARD_THEMES = {
  occupation: {
    frame: "#0b6e33",         // dark green edge
    body: "#009243",          // green field
    plate: "#f6f27b",         // pale yellow title/text plates
    plateBorder: "#0b6e33",
    stripe: "rgba(255,255,255,0.9)",
    artRing: "#f6d800",       // yellow ring around circular art
    artShape: "circle",
    label: "Occupation",
  },
  minor: {
    frame: "#cc2a1e",         // red edge
    body: "#e87c14",          // orange field
    plate: "#fdfb72",
    plateBorder: "#cc2a1e",
    stripe: "rgba(255,255,255,0.9)",
    artRing: "#fdfb72",
    artShape: "hex",
    label: "Minor Improvement",
  },
  major: {
    frame: "#7f1d1d",         // deep red-brown edge
    body: "#c0392b",          // brick red field
    plate: "#fdf3d0",
    plateBorder: "#7f1d1d",
    stripe: "rgba(255,255,255,0.75)",
    artRing: "#fdf3d0",
    artShape: "square",
    label: "Major Improvement",
  },
};

const SERIF = `Georgia, 'Times New Roman', serif`;

// ── Card text markup ────────────────────────────────────────
// Card text is plain English, optionally enriched with tokens:
//   {wood} {food} …        → inline resource icon
//   {vp} / {vp:2}          → victory point disc (with number)
//   {pass}                 → passing-card arrow
//   {action}               → card-action marker
//   {->}                   → thin arrow
// Unknown tokens render as literal text so un-enriched database
// text always degrades gracefully.
const TOKEN_RE = /\{([a-z>-]+)(?::(-?\d+))?\}/gi;

// Official cards set parenthetical clarifications in italic.
function withItalics(str, key) {
  const out = [];
  let last = 0, m;
  const re = /\([^)]*\)/g;
  while ((m = re.exec(str))) {
    if (m.index > last) out.push(str.slice(last, m.index));
    out.push(<i key={`i${key}-${m.index}`}>{m[0]}</i>);
    last = m.index + m[0].length;
  }
  if (last < str.length) out.push(str.slice(last));
  return out;
}

export function CardText({ text, style }) {
  const parts = [];
  let last = 0, m, key = 0;
  TOKEN_RE.lastIndex = 0;
  while ((m = TOKEN_RE.exec(text || ""))) {
    if (m.index > last) parts.push(...withItalics(text.slice(last, m.index), key++));
    const name = m[1].toLowerCase();
    if (ICONS[name]) {
      parts.push(<span key={key++} title={ICONS[name].label}>{ICONS[name].icon}</span>);
    } else if (name === "vp") {
      parts.push(<VPBadge key={key++} points={m[2] != null ? +m[2] : null} inline />);
    } else if (name === "pass") {
      parts.push(<PassMark key={key++} inline />);
    } else if (name === "action") {
      parts.push(<ActionMark key={key++} inline />);
    } else if (name === "->" || name === ">") {
      parts.push("→");
    } else {
      parts.push(m[0]);
    }
    last = m.index + m[0].length;
  }
  if (last < (text || "").length) parts.push(...withItalics(text.slice(last), key++));
  return <span style={style}>{parts}</span>;
}

// Font size (em) that fits a run of text into its plate.
function fitSize(len, steps) {
  for (const [max, size] of steps) if (len <= max) return size;
  return steps[steps.length - 1][1];
}

// ── Badges & plates ─────────────────────────────────────────

export function VPBadge({ points, inline }) {
  if (points == null || points === 0) return null;
  const base = {
    display: "inline-flex", alignItems: "center", justifyContent: "center",
    width: inline ? "1.5em" : "2.6em", height: inline ? "1.5em" : "2.6em",
    borderRadius: "50%", background: "#f6d800",
    border: "0.14em solid #6b5900", color: "#1c1917",
    fontWeight: 700, fontFamily: SERIF,
    fontSize: inline ? "0.9em" : "1.3em",
    verticalAlign: inline ? "-0.3em" : undefined,
    boxShadow: inline ? "none" : "0 0.1em 0.3em rgba(0,0,0,0.4)",
  };
  return <span style={base} title={`${points} point${Math.abs(points) === 1 ? "" : "s"}`}>{points}</span>;
}

export function MinPlayersBadge({ n }) {
  return (
    <span title={`Playable with ${n}+ players`} style={{
      display: "inline-flex", alignItems: "center", justifyContent: "center",
      width: "2.6em", height: "2.6em", background: "#312e81",
      border: "0.14em solid #14124d", borderRadius: "0.3em",
      color: "#fff", fontWeight: 700, fontFamily: SERIF, fontSize: "1.2em",
      boxShadow: "0 0.1em 0.3em rgba(0,0,0,0.4)",
    }}>{n}+</span>
  );
}

export function DeckBadge({ deck }) {
  if (!deck || deck === "base") return null;
  const custom = deck === "custom";
  return (
    <span title={custom ? "Custom card" : `Deck ${deck}`} style={{
      display: "inline-flex", alignItems: "center", justifyContent: "center",
      minWidth: "2.4em", height: "2.4em", padding: "0 0.3em",
      borderRadius: "50%", background: "#fff",
      border: "0.14em solid rgba(0,0,0,0.55)", color: "#1c1917",
      fontWeight: 700, fontFamily: SERIF, fontSize: "1.05em",
      boxShadow: "0 0.1em 0.3em rgba(0,0,0,0.35)",
    }}>{custom ? "✎" : deck}</span>
  );
}

// Cost as a vertical stack of count+icon chips (top-right corner).
// costText covers irregular costs (e.g. "1W or 1C") shown verbatim.
export function CostChips({ cost, costText }) {
  const entries = Object.entries(cost || {});
  if (!entries.length && costText) {
    return (
      <span title={`Costs ${costText}`} style={{
        display: "inline-block", background: "#fff", borderRadius: "1em",
        border: "0.12em solid rgba(0,0,0,0.5)", padding: "0.15em 0.45em",
        fontWeight: 700, fontFamily: SERIF, fontSize: "0.75em",
        color: "#1c1917", maxWidth: "9em", textAlign: "center", lineHeight: 1.15,
      }}>{costText}</span>
    );
  }
  if (!entries.length) return null;
  return (
    <span style={{ display: "inline-flex", flexDirection: "column", gap: "0.25em", alignItems: "flex-end" }}>
      {entries.map(([good, n]) => (
        <span key={good} title={`Costs ${n} ${ICONS[good]?.label || good}`} style={{
          display: "inline-flex", alignItems: "center", gap: "0.15em",
          background: "#fff", borderRadius: "1em",
          border: "0.12em solid rgba(0,0,0,0.5)",
          padding: "0.05em 0.4em", fontWeight: 700, fontFamily: SERIF,
          fontSize: "1.05em", color: "#1c1917",
        }}>{n}{ICONS[good]?.icon || good}</span>
      ))}
    </span>
  );
}

export function PrereqPlate({ text }) {
  if (!text) return null;
  return (
    <span title={`Prerequisite: ${text}`} style={{
      display: "inline-block", background: "#fff",
      borderRadius: "0.4em", border: "0.1em solid rgba(0,0,0,0.35)",
      padding: "0.2em 0.35em", fontFamily: SERIF, color: "#1c1917",
      fontSize: fitSize(text.length, [[18, "0.8em"], [34, "0.7em"], [999, "0.6em"]]),
      lineHeight: 1.15, maxWidth: "7.5em", textAlign: "center",
    }}>{text}</span>
  );
}

// Passing / "traveling" cards move to the next player after use.
export function PassMark({ inline }) {
  return (
    <span title="Passed to the next player after playing" style={{
      display: "inline-flex", alignItems: "center", justifyContent: "center",
      width: inline ? "1.4em" : "2em", height: inline ? "1.4em" : "2em",
      borderRadius: "50%", background: "#fff",
      border: "0.12em solid rgba(0,0,0,0.5)", color: "#1c1917",
      fontSize: inline ? "0.9em" : "1.1em", fontWeight: 700,
      verticalAlign: inline ? "-0.25em" : undefined,
    }}>↪</span>
  );
}

// Cards that grant an extra action space.
export function ActionMark({ inline }) {
  return (
    <span title="Grants a card action" style={{
      display: "inline-flex", alignItems: "center", justifyContent: "center",
      width: inline ? "1.4em" : "2em", height: inline ? "1.4em" : "2em",
      borderRadius: "0.3em", background: "#b91c1c",
      border: "0.12em solid #7f1d1d", color: "#fff",
      fontSize: inline ? "0.8em" : "1em", fontWeight: 700,
      verticalAlign: inline ? "-0.25em" : undefined,
    }}>▶</span>
  );
}

// ── Art window ──────────────────────────────────────────────

const HEX_CLIP = "polygon(50% 0%, 94% 25%, 94% 75%, 50% 100%, 6% 75%, 6% 25%)";

export function ArtWindow({ theme, artUrl, alt, glyph }) {
  const [failed, setFailed] = useState(false);
  const circle = theme.artShape === "circle";
  const hex = theme.artShape === "hex";
  const outer = {
    width: "10.6em", height: "10.6em", flexShrink: 0,
    borderRadius: circle ? "50%" : hex ? 0 : "0.6em",
    clipPath: hex ? HEX_CLIP : undefined,
    border: hex ? "none" : `0.35em solid ${theme.artRing}`,
    background: hex ? theme.artRing : theme.artRing,
    display: "flex", alignItems: "center", justifyContent: "center",
    overflow: "hidden", position: "relative",
    boxShadow: hex ? "none" : "0 0.15em 0.5em rgba(0,0,0,0.35)",
  };
  const placeholder = (
    <div style={{
      width: "100%", height: "100%", display: "flex",
      alignItems: "center", justifyContent: "center",
      background: `radial-gradient(circle at 50% 38%, #fff8dc 0%, ${theme.body} 130%)`,
      fontSize: "4.2em",
    }}>{glyph}</div>
  );
  const artScale = hex ? { width: "94%", height: "94%", clipPath: HEX_CLIP } : { width: "100%", height: "100%" };
  return (
    <div style={outer}>
      {artUrl && !failed ? (
        <img src={artUrl} alt={alt} onError={() => setFailed(true)}
          style={{ ...artScale, objectFit: "cover", display: "block" }} />
      ) : placeholder}
    </div>
  );
}

// ── Title plate ─────────────────────────────────────────────

export function TitlePlate({ theme, name, traveling }) {
  return (
    <div style={{
      background: theme.plate, border: `0.14em solid ${theme.plateBorder}`,
      borderRadius: "0.8em", padding: "0.3em 0.5em",
      display: "flex", alignItems: "center", justifyContent: "center", gap: "0.3em",
      minHeight: "2.6em", flex: 1,
      boxShadow: "inset 0 0 0.4em rgba(255,255,255,0.6)",
    }}>
      <span style={{
        fontFamily: SERIF, fontWeight: 700, color: "#1c1917",
        textAlign: "center", lineHeight: 1.1,
        fontSize: fitSize((name || "").length, [[13, "1.5em"], [19, "1.3em"], [26, "1.1em"], [999, "0.95em"]]),
      }}>{name}</span>
      {traveling ? <PassMark inline /> : null}
    </div>
  );
}

// ── Frame + full card ───────────────────────────────────────

export function CardFrame({ theme, width, selected, playable, onClick, children, title }) {
  const height = Math.round(width * 1.545);
  return (
    <div onClick={onClick} title={title} style={{
      width, height, fontSize: width / 25, flexShrink: 0,
      background: theme.body, border: `0.55em solid ${theme.frame}`,
      borderRadius: "1.1em", position: "relative", overflow: "hidden",
      display: "flex", flexDirection: "column",
      cursor: onClick ? "pointer" : "default",
      boxShadow: selected
        ? "0 0 0 0.35em #d97706, 0 0.3em 0.8em rgba(0,0,0,0.35)"
        : "0 0.2em 0.6em rgba(0,0,0,0.3)",
      filter: playable === false ? "grayscale(0.7) brightness(0.85)" : "none",
      opacity: playable === false ? 0.75 : 1,
      userSelect: "none",
    }}>{children}</div>
  );
}

// Normalize a catalog entry (agricola_cards.json) to render fields.
function normalize(spec, cid) {
  const type = spec.type === "occupation" ? "occupation"
    : spec.type === "major" ? "major" : "minor";
  return {
    type,
    name: spec.name || cid,
    text: spec.text || "",
    deck: spec.deck,
    cost: spec.cost || {},
    costText: spec.cost_text || "",
    points: spec.points ?? spec.vp ?? 0,
    minPlayers: spec.min_players || null,
    prereq: spec.prereq_text || "",
    traveling: !!spec.traveling,
    hasAction: !!spec.has_card_action,
  };
}

const TYPE_GLYPH = { occupation: "👨‍🌾", minor: "🧺", major: "🏺" };

export function defaultArtUrl(cid) {
  return `${import.meta.env.BASE_URL}agricola/art/${cid}.jpg`;
}

// ============================================================
// Asset-layered card (v2) — real frame/badge artwork with text
// blocks positioned over it, instead of CSS-drawn plates.
//
// Layer stack (bottom to top):
//   1. art image, circle-cropped, slightly larger than the
//      frame's transparent window so it tucks under the ring
//   2. frame WebP (transparent exterior + art window)
//   3. badge images (min-players plaque, deck disc) with text
//      overlaid, plus the title (SVG textPath along the plate
//      arch), card code, and rules text block
//
// All geometry is in fractions of the frame image (1010x1558),
// measured from the asset during processing.
// ============================================================

const FRAME_ASPECT = 1558 / 1010;             // height / width
const WINDOW = { cx: 0.4985, cy: 0.3479, r: 0.2597 };  // r as fraction of width

function frameUrl(name) {
  return `${import.meta.env.BASE_URL}agricola/frames/${name}.webp`;
}

// A badge asset with text centered on it (e.g. "1+" on the
// purple plaque, the deck letter on the disc).
function Badge({ asset, label, size, fontSize, title, dark }) {
  return (
    <span title={title} style={{
      position: "relative", width: size, height: size,
      display: "inline-flex", alignItems: "center", justifyContent: "center",
      flexShrink: 0,
    }}>
      <img src={frameUrl(asset)} alt="" draggable={false}
        style={{ position: "absolute", inset: 0, width: "100%", height: "100%" }} />
      <span style={{
        position: "relative", fontFamily: "Alegreya, Georgia, serif",
        fontWeight: 800, fontSize, lineHeight: 1,
        color: dark ? "#241a05" : "#fff",
        textShadow: dark ? "none" : "0 0.05em 0.15em rgba(0,0,0,0.7)",
      }}>{label}</span>
    </span>
  );
}

// Card name arched along the top plate, matching the official
// occupation title treatment. Rendered as SVG text on a path so
// it scales with the card.
function TitleArc({ name }) {
  const arcId = useId();
  const size = fitSize((name || "").length,
    [[12, 92], [17, 78], [22, 66], [28, 56], [999, 48]]);
  return (
    <svg viewBox="0 0 1010 1558" style={{
      position: "absolute", inset: 0, width: "100%", height: "100%",
      pointerEvents: "none",
    }}>
      <path id={arcId} d="M 80 320 Q 505 130 930 320" fill="none" />
      <text fontFamily="Alegreya, Georgia, serif" fontWeight="800"
        fontSize={size} fill="#241a05">
        <textPath href={`#${arcId}`} startOffset="50%" textAnchor="middle">
          {name}
        </textPath>
      </text>
    </svg>
  );
}

export function OccupationCardV2({ spec, cid, width = 250, artUrl, playable, selected, onClick }) {
  const c = normalize(spec || {}, cid);
  const art = artUrl !== undefined ? artUrl : (cid ? defaultArtUrl(cid) : null);
  const [artFailed, setArtFailed] = useState(false);
  const height = Math.round(width * FRAME_ASPECT);
  const em = width / 25;

  // Art window in px: radius is a fraction of width; extend it a
  // touch so the art tucks under the frame's ring.
  const artR = (WINDOW.r + 0.01) * width;
  const artStyle = {
    position: "absolute",
    left: WINDOW.cx * width - artR, top: WINDOW.cy * height - artR,
    width: artR * 2, height: artR * 2, borderRadius: "50%", overflow: "hidden",
    display: "flex", alignItems: "center", justifyContent: "center",
    background: `radial-gradient(circle at 50% 38%, #fff8dc 0%, #cadd6e 130%)`,
  };

  return (
    <div onClick={onClick} title={`${c.name} — Occupation`} style={{
      position: "relative", width, height, fontSize: em, flexShrink: 0,
      cursor: onClick ? "pointer" : "default", userSelect: "none",
      filter: playable === false ? "grayscale(0.7) brightness(0.85)" : "none",
      opacity: playable === false ? 0.75 : 1,
      borderRadius: "0.9em",
      boxShadow: selected ? "0 0 0 0.35em #d97706" : "none",
    }}>
      {/* 1 — art layer under the frame window */}
      <div style={artStyle}>
        {art && !artFailed
          ? <img src={art} alt={c.name} onError={() => setArtFailed(true)} draggable={false}
              style={{ width: "100%", height: "100%", objectFit: "cover", display: "block" }} />
          : <span style={{ fontSize: "4.2em" }}>{TYPE_GLYPH.occupation}</span>}
      </div>

      {/* 2 — frame artwork */}
      <img src={frameUrl("occupation")} alt="" draggable={false} style={{
        position: "absolute", inset: 0, width: "100%", height: "100%",
        pointerEvents: "none",
      }} />

      {/* 3 — title, code, badges, rules text */}
      <TitleArc name={c.name} />

      {/* official-style card code — only short codes fit the plate */}
      {cid && cid.length <= 6 ? (
        <span style={{
          position: "absolute", left: "8%", top: "24%",
          fontFamily: "Alegreya, Georgia, serif", fontWeight: 700,
          fontSize: "0.72em", color: "#4a3d15", opacity: 0.85,
        }}>{cid}</span>
      ) : null}

      {/* gold band, left: min players */}
      {c.minPlayers ? (
        <span style={{ position: "absolute", left: "7.5%", top: "45%" }}>
          <Badge asset="badge_players" label={`${c.minPlayers}+`}
            size={0.13 * width} fontSize="1.35em"
            title={`Playable with ${c.minPlayers}+ players`} />
        </span>
      ) : null}

      {/* gold band, right: VP, marks, deck */}
      <span style={{
        position: "absolute", right: "7.5%", top: "45%",
        display: "flex", gap: "0.25em", alignItems: "center",
      }}>
        {c.traveling ? <PassMark /> : null}
        {c.hasAction ? <ActionMark /> : null}
        {c.points ? (
          <Badge asset="badge_vp" label={c.points} size={0.13 * width}
            fontSize="1.5em" dark title={`${c.points} points`} />
        ) : null}
        {c.deck && c.deck !== "base" ? (
          <Badge asset="badge_deck" label={c.deck === "custom" ? "✎" : c.deck}
            size={0.13 * width} fontSize={c.deck.length > 1 ? "1.1em" : "1.5em"}
            title={c.deck === "custom" ? "Custom card" : `Deck ${c.deck}`} />
        ) : null}
      </span>

      {/* rules text block on the big plate */}
      <div style={{
        position: "absolute", left: "11%", right: "11%", top: "57%", bottom: "6.5%",
        display: "flex", flexDirection: "column",
        alignItems: "center", justifyContent: "center", textAlign: "center",
        overflow: "hidden",
      }}>
        {c.prereq ? (
          <div style={{
            fontFamily: "Alegreya, Georgia, serif", fontStyle: "italic",
            fontWeight: 500, fontSize: "0.85em", color: "#4a3d15", marginBottom: "0.4em",
          }}>Requires {c.prereq}</div>
        ) : null}
        <CardText text={c.text} style={{
          fontFamily: "Alegreya, Georgia, serif", fontWeight: 500,
          color: "#241a05", lineHeight: 1.24,
          fontSize: fitSize((c.text || "").length,
            [[60, "1.5em"], [110, "1.3em"], [170, "1.14em"], [240, "1.0em"], [320, "0.9em"], [999, "0.8em"]]),
        }} />
      </div>
    </div>
  );
}

/**
 * The full official-layout card.
 *   spec     — catalog entry from agricola_cards.json
 *   cid      — card id (used for default art lookup + footer code)
 *   width    — rendered width in px (height follows card ratio)
 *   artUrl   — override art image (default: /agricola/art/<cid>.jpg)
 *   playable / selected / onClick — same semantics as HandCard
 *   footer   — optional React node rendered in the bottom strip
 */
export function AgricolaCard({ spec, cid, width = 250, artUrl, playable, selected, onClick, footer }) {
  const c = normalize(spec || {}, cid);
  // Occupations use the asset-layered builder; minors/majors keep
  // the CSS frame until their frame artwork exists.
  if (c.type === "occupation") {
    return <OccupationCardV2 spec={spec} cid={cid} width={width} artUrl={artUrl}
      playable={playable} selected={selected} onClick={onClick} />;
  }
  const theme = CARD_THEMES[c.type];
  const art = artUrl !== undefined ? artUrl : (cid ? defaultArtUrl(cid) : null);
  const isOcc = c.type === "occupation";

  return (
    <CardFrame theme={theme} width={width} selected={selected} playable={playable} onClick={onClick}
      title={`${c.name} — ${theme.label}`}>

      {/* Header: prereq | title | cost */}
      <div style={{ display: "flex", alignItems: "flex-start", gap: "0.4em", padding: "0.5em 0.5em 0.2em" }}>
        {c.prereq ? <PrereqPlate text={c.prereq} /> : null}
        <TitlePlate theme={theme} name={c.name} traveling={c.traveling} />
        {Object.keys(c.cost).length || c.costText ? <CostChips cost={c.cost} costText={c.costText} /> : null}
      </div>

      {/* Art band: striped field, art window, corner badges */}
      <div style={{
        position: "relative", height: "11.6em",
        display: "flex", alignItems: "center", justifyContent: "center",
        backgroundImage: `repeating-linear-gradient(180deg, transparent 0 1.1em, ${theme.stripe} 1.1em 1.5em)`,
      }}>
        <ArtWindow theme={theme} artUrl={art} alt={c.name} glyph={TYPE_GLYPH[c.type]} />
        <span style={{ position: "absolute", left: "0.5em", bottom: "0.4em" }}>
          {isOcc && c.minPlayers ? <MinPlayersBadge n={c.minPlayers} /> : <VPBadge points={c.points} />}
        </span>
        <span style={{ position: "absolute", right: "0.5em", bottom: "0.4em", display: "flex", gap: "0.3em", alignItems: "center" }}>
          {isOcc && c.points ? <VPBadge points={c.points} /> : null}
          <DeckBadge deck={c.deck} />
        </span>
      </div>

      {/* Rules text plate */}
      <div style={{ flex: 1, display: "flex", padding: "0.4em 0.7em 0.6em", minHeight: 0 }}>
        <div style={{
          flex: 1, background: theme.plate,
          border: `0.14em solid ${theme.plateBorder}`, borderRadius: "1em",
          padding: "0.5em 0.7em", display: "flex", flexDirection: "column",
          alignItems: "center", justifyContent: "center", textAlign: "center",
          overflow: "hidden",
        }}>
          <CardText text={c.text} style={{
            fontFamily: SERIF, color: "#1c1917", lineHeight: 1.25,
            fontSize: fitSize((c.text || "").length, [[60, "1.3em"], [110, "1.12em"], [170, "0.98em"], [240, "0.88em"], [320, "0.78em"], [999, "0.7em"]]),
          }} />
        </div>
      </div>

      {/* Footer strip: card code + markers */}
      <div style={{
        display: "flex", alignItems: "center", justifyContent: "space-between",
        padding: "0 0.7em 0.35em", minHeight: "1.3em",
      }}>
        <span style={{ fontFamily: SERIF, fontSize: "0.75em", color: "rgba(255,255,255,0.8)" }}>{cid || ""}</span>
        <span style={{ display: "flex", gap: "0.3em" }}>
          {c.hasAction ? <ActionMark /> : null}
          {footer}
        </span>
      </div>
    </CardFrame>
  );
}

export default AgricolaCard;
