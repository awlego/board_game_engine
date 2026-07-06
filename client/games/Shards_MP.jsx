import { useState, useEffect, useRef, useCallback, useMemo } from "react";

// ─── CONFIGURATION ─────────────────────────────────────────────────
import { WS_URL } from "../ws.js";

// Shard art lives in client/public/shards-art/ so it survives a
// VITE_BASE=/games/engine/ build.
const artUrl = (shardId) => `${import.meta.env.BASE_URL}shards-art/${shardId}.jpeg`;

// ─── SHARD METADATA (mirrors server/shards data) ───────────────────
// Names, colors, ability text and trump text are static card data; the
// server view only carries shard ids, so the client keeps this table.

const SHARDS = {
  autonomy: {
    id: "autonomy", name: "Autonomy", color: "#b3452c",
    abilities: {
      a1: { text: "You may discard an Autonomy card from your hand. If you do, draw a card." },
      a2: { text: "This card's rank has +2 for each Autonomy card in other players' scoring areas." },
    },
    trumpText: "You may not lead with Autonomy unless you have no other Shard in your hand.",
  },
  cultivation: {
    id: "cultivation", name: "Cultivation", color: "#6b8f2e",
    abilities: {
      a1: { text: "Reveal the top card of the deck. You may discard the revealed card to add its rank to this card, or put it back." },
      a2: { text: "Discard up to two cards, then draw that many cards." },
    },
    trumpText: "At the start of the round, all players draw additional cards as follows, then all simultaneously discard that many cards. Round 1: Draw 3 cards. Round 2: Draw 2 cards. Round 3: Draw 1 card.",
  },
  devotion: {
    id: "devotion", name: "Devotion", color: "#7b4b94",
    abilities: {
      a1: { text: "At the end of the game, if this card is in your scoring area, you must treat it as any Shard except Devotion." },
      a2: { text: "When you play this, you may exchange it with a card in your scoring area and activate its effect." },
    },
    trumpText: "Place a card in your scoring area at the start of the round instead of the end of the round. Play all 10 tricks, with no cards remaining after the last trick.",
  },
  dominion: {
    id: "dominion", name: "Dominion", color: "#8a6d3b",
    abilities: {
      a1: { text: "At the end of the game, if this card is in your scoring area, treat one of your non-Dominion scoring cards as Dominion." },
      a2: { text: "Draw a card. Discard the card in your hand with the lowest rank." },
    },
    trumpText: "The winner of each trick must choose the card with the highest rank to place in their scoring area.",
  },
  honor: {
    id: "honor", name: "Honor", color: "#2e6e8f",
    abilities: {},
    trumpText: "No trump ability.",
  },
  odium: {
    id: "odium", name: "Odium", color: "#b89b2c",
    abilities: {
      a1: { text: "Discard a card, then take a random card from another player's hand. Then they draw a card." },
      a2: { text: "This card's rank has +2 for each different Shard in your scoring area." },
    },
    trumpText: "At the end of the round, if one player has fewer cards in their scoring area than any other player, they may steal a card from the scoring area with the most (or tied for most).",
  },
  preservation: {
    id: "preservation", name: "Preservation", color: "#5f8f7b",
    abilities: {
      a1: { text: "Draw a card, then discard a card." },
      a2: { text: "This card's rank has +1 for each Preservation card in your scoring area." },
    },
    trumpText: "The winner of each trick may choose a Preservation card from the discard pile to place in their scoring area instead of a played card.",
  },
  ruin: {
    id: "ruin", name: "Ruin", color: "#5a3535",
    abilities: {
      a1: { text: "At the end of the game, if this card is in your scoring area, score -2 points." },
      a2: { text: "Reveal the top card of the deck. You may discard the revealed card to subtract its rank from another player's played card, or put it back." },
    },
    trumpText: "The winner of each trick must choose the card with the lowest rank to place in their scoring area.",
  },
};

const shardOf = (id) => SHARDS[id] ?? { id, name: id, color: "#888", abilities: {} };

// ─── STYLES ────────────────────────────────────────────────────────
// Ported from the original "illuminated card table" stylesheet. Every
// selector is scoped under .shards-root so nothing leaks into the shared
// game-selector menu.

const CSS = `
.shards-root {
  --night-0: #0b101f;
  --night-1: #121a33;
  --night-2: #1a2340;
  --night-3: #222d52;
  --gold: #c9a959;
  --gold-bright: #e8c979;
  --line: rgba(201, 169, 89, .28);
  --line-dim: rgba(201, 169, 89, .14);
  --parchment: #f4ead2;
  --parchment-2: #e9dcbc;
  --ink: #2a2419;
  --ink-dim: #6d6350;
  --text: #e9e2cf;
  --text-dim: #a49d86;
  --select: #7cc4ff;
  --font-display: 'Cinzel', 'Times New Roman', serif;
  --font-body: 'Alegreya Sans', system-ui, sans-serif;

  position: relative;
  margin: 0;
  font-family: var(--font-body);
  font-size: 16px;
  background:
    radial-gradient(1100px 520px at 50% -80px, #2b3a6b 0%, transparent 70%),
    radial-gradient(900px 700px at 85% 110%, #1d2750 0%, transparent 65%),
    linear-gradient(180deg, var(--night-1), var(--night-0));
  background-attachment: fixed;
  color: var(--text);
  min-height: 100vh;
}
.shards-root * { box-sizing: border-box; }
/* faint grain so the felt doesn't band */
.shards-root::before {
  content: '';
  position: fixed;
  inset: 0;
  pointer-events: none;
  opacity: .05;
  background-image: url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='160' height='160'%3E%3Cfilter id='n'%3E%3CfeTurbulence type='fractalNoise' baseFrequency='.9' numOctaves='2'/%3E%3C/filter%3E%3Crect width='160' height='160' filter='url(%23n)'/%3E%3C/svg%3E");
}

.shards-root .mono { font-family: ui-monospace, monospace; }

/* --- header --- */

.shards-root .sh-header {
  display: flex;
  align-items: center;
  gap: .75rem;
  padding: .55rem 1.1rem .55rem 8.5rem;
  border-bottom: 1px solid var(--line-dim);
}
.shards-root .sh-header h1 {
  flex: 1;
  margin: 0;
  font-family: var(--font-display);
  font-size: 1.05rem;
  font-weight: 600;
  letter-spacing: .14em;
  text-transform: uppercase;
  color: var(--gold-bright);
  text-shadow: 0 1px 6px rgba(0, 0, 0, .6);
}

.shards-root .conn { display: inline-flex; align-items: center; gap: .4rem; font-size: .75rem; color: var(--text-dim); }
.shards-root .conn i {
  width: 8px; height: 8px; border-radius: 50%;
  background: #d05555;
  box-shadow: 0 0 6px #d0555588;
}
.shards-root .conn.ok i { background: #7fc97f; box-shadow: 0 0 6px #7fc97f88; }
.shards-root .conn.ok .conn-label { visibility: hidden; width: 0; overflow: hidden; display: inline-block; }

/* --- shared bits --- */

.shards-root .screen { padding: 1.25rem 1rem 2rem; max-width: 860px; margin: 0 auto; }

.shards-root .panel {
  background: linear-gradient(180deg, var(--parchment), var(--parchment-2));
  border: 1px solid #8a763f;
  border-radius: 14px;
  padding: 1.35rem 1.6rem 1.5rem;
  color: var(--ink);
  box-shadow:
    inset 0 0 0 1px rgba(255, 252, 240, .6),
    inset 0 0 60px rgba(138, 109, 59, .12),
    0 10px 40px rgba(0, 0, 0, .5);
}
.shards-root .panel h2 {
  margin: 0 0 .9rem;
  font-family: var(--font-display);
  font-weight: 700;
  font-size: 1.35rem;
  letter-spacing: .04em;
}
.shards-root .panel h3 {
  margin: 1.1rem 0 .45rem;
  font-family: var(--font-display);
  font-size: .85rem;
  font-weight: 600;
  letter-spacing: .1em;
  text-transform: uppercase;
  color: var(--ink-dim);
}
.shards-root .panel .hint { margin: -.4rem 0 .8rem; color: var(--ink-dim); font-size: .92rem; }

.shards-root .row { display: flex; gap: .6rem; margin-top: 1.25rem; flex-wrap: wrap; }

.shards-root button {
  font-family: var(--font-display);
  font-size: .82rem;
  font-weight: 600;
  letter-spacing: .07em;
  padding: .5rem 1.05rem;
  border-radius: 8px;
  border: 1px solid #9c8952;
  background: transparent;
  color: inherit;
  cursor: pointer;
  transition: border-color .15s, color .15s, filter .15s, box-shadow .15s;
}
.shards-root button:hover:not(:disabled) { border-color: var(--gold); filter: brightness(1.08); }
.shards-root button.primary {
  background: linear-gradient(180deg, #e3c67e, #b3903f);
  border-color: #8a6d2f;
  color: #241b08;
  box-shadow: inset 0 1px 0 rgba(255, 255, 255, .45), 0 2px 8px rgba(0, 0, 0, .35);
}
.shards-root button:disabled { opacity: .45; cursor: default; }

.shards-root .field { margin: 0 0 .9rem; text-align: left; }
.shards-root .field label {
  display: block;
  margin-bottom: .3rem;
  font-family: var(--font-display);
  font-size: .72rem;
  font-weight: 600;
  letter-spacing: .12em;
  text-transform: uppercase;
  color: var(--ink-dim);
}
.shards-root .field input {
  width: 100%;
  font-family: var(--font-body);
  font-size: 1rem;
  padding: .5rem .7rem;
  border-radius: 8px;
  border: 1px solid #9c8952;
  background: rgba(255, 252, 240, .55);
  color: var(--ink);
  outline: none;
}
.shards-root .field input:focus { border-color: #8a6d2f; box-shadow: 0 0 0 2px rgba(201, 169, 89, .35); }
.shards-root .field input.code { letter-spacing: .35em; font-size: 1.3rem; text-align: center; font-weight: 700; }

/* --- game lobby --- */

.shards-root .lobby-players { list-style: none; padding: 0; margin: 0; }
.shards-root .lobby-players li { display: flex; align-items: center; gap: .55rem; padding: .3rem 0; font-weight: 500; }
.shards-root .lobby-players .host-tag { font-size: .7rem; letter-spacing: .08em; text-transform: uppercase; color: var(--ink-dim); }
.shards-root .lobby-players .offline { font-size: .7rem; letter-spacing: .08em; text-transform: uppercase; color: #a04b3a; }

.shards-root .avatars { display: inline-flex; margin-right: .1rem; }
.shards-root .avatars i {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 26px; height: 26px;
  margin-left: -8px;
  border-radius: 50%;
  background: radial-gradient(circle at 35% 30%, #3a4a80, var(--night-2));
  border: 1px solid var(--gold);
  color: var(--gold-bright);
  font-style: normal;
  font-family: var(--font-display);
  font-size: .68rem;
  font-weight: 700;
}
.shards-root .avatars i:first-child { margin-left: 0; }

.shards-root .room-code {
  font-family: ui-monospace, monospace;
  font-size: 2.1rem;
  font-weight: 700;
  letter-spacing: .35em;
  text-align: center;
  padding: .55rem 0 .55rem .35em;
  margin: .2rem 0 .6rem;
  border-radius: 10px;
  border: 1px dashed #9c8952;
  background: rgba(255, 252, 240, .45);
  user-select: all;
}

/* --- game table layout --- */

.shards-root .screen-game {
  max-width: 1440px;
  margin: 0 auto;
  padding: .9rem 1rem 2rem;
  display: grid;
  grid-template-columns: minmax(0, 1fr) 290px;
  gap: 1.1rem;
  align-items: start;
}

.shards-root .table-wrap {
  display: flex;
  flex-direction: column;
  gap: .8rem;
  min-height: calc(100vh - 110px);
}

.shards-root .side { position: sticky; top: .9rem; }

/* --- seats --- */

.shards-root .opponents { display: flex; gap: .8rem; flex-wrap: wrap; }
.shards-root .opp {
  position: relative;
  flex: 1;
  min-width: 190px;
  padding: .55rem .8rem .6rem;
  border-radius: 12px;
  border: 1px solid var(--line-dim);
  background: linear-gradient(180deg, rgba(255, 255, 255, .05), rgba(255, 255, 255, .02));
}
.shards-root .opp.turn {
  border-color: var(--gold);
  animation: shards-seat-pulse 2.2s ease-in-out infinite;
}
@keyframes shards-seat-pulse {
  0%, 100% { box-shadow: 0 0 0 0 rgba(232, 201, 121, 0), inset 0 0 18px rgba(232, 201, 121, .05); }
  50% { box-shadow: 0 0 18px 2px rgba(232, 201, 121, .28), inset 0 0 18px rgba(232, 201, 121, .1); }
}

.shards-root .seat-head { display: flex; align-items: baseline; gap: .5rem; }
.shards-root .seat-head .name { font-weight: 700; font-size: 1.02rem; }
.shards-root .seat-head .lead {
  font-size: .62rem;
  font-weight: 700;
  letter-spacing: .1em;
  text-transform: uppercase;
  color: var(--gold-bright);
  border: 1px solid var(--line);
  border-radius: 999px;
  padding: .05rem .4rem;
}
.shards-root .seat-head .handcount {
  margin-left: auto;
  display: inline-flex;
  align-items: center;
  gap: .3rem;
  font-size: .85rem;
  color: var(--text-dim);
}
.shards-root .cardback {
  display: inline-block;
  width: 12px; height: 16px;
  border-radius: 2px;
  background: linear-gradient(135deg, #34437a, #1a2347);
  border: 1px solid rgba(201, 169, 89, .55);
  box-shadow: 3px 2px 0 -1px #0e1428, 3px 2px 0 0 rgba(201, 169, 89, .35);
  margin-right: 3px;
}

.shards-root .chips { display: flex; gap: .3rem; flex-wrap: wrap; margin-top: .35rem; min-height: 20px; }
.shards-root .chip {
  --shard: #888;
  display: inline-flex;
  align-items: center;
  gap: .28rem;
  font-size: .78rem;
  font-weight: 700;
  color: var(--text);
  padding: .06rem .5rem .06rem .12rem;
  border-radius: 999px;
  border: 1px solid color-mix(in srgb, var(--shard) 75%, #fff);
  background: color-mix(in srgb, var(--shard) 40%, transparent);
}
.shards-root .chip i {
  width: 16px; height: 16px;
  border-radius: 50%;
  background-size: cover;
  background-position: center;
  border: 1px solid rgba(0, 0, 0, .5);
}
.shards-root .chip.ab-chip { border-color: var(--line); background: rgba(232, 201, 121, .12); color: var(--gold-bright); padding-left: .5rem; }

/* --- table center --- */

.shards-root .table-center {
  flex: 1;
  display: flex;
  flex-direction: column;
  justify-content: center;
  gap: .4rem;
  padding: 1rem;
  border-radius: 16px;
  border: 1px solid var(--line-dim);
  background:
    radial-gradient(75% 90% at 50% 45%, rgba(255, 255, 255, .045), transparent 75%),
    rgba(0, 0, 0, .18);
  text-align: center;
}

.shards-root .round-info { display: flex; justify-content: center; gap: 1.4rem; flex-wrap: wrap; }
.shards-root .round-info .stat {
  font-size: .78rem;
  letter-spacing: .09em;
  text-transform: uppercase;
  color: var(--text-dim);
}
.shards-root .round-info .stat b { color: var(--text); font-size: .9rem; letter-spacing: 0; }

.shards-root .trump { display: flex; flex-direction: column; align-items: center; gap: .25rem; margin: .3rem 0 .4rem; }
.shards-root .trump-line { display: flex; align-items: center; gap: .5rem; }
.shards-root .trump-label {
  font-family: var(--font-display);
  font-size: .72rem;
  font-weight: 600;
  letter-spacing: .18em;
  text-transform: uppercase;
  color: var(--text-dim);
}
.shards-root .trump-chip {
  --shard: #888;
  display: inline-flex;
  align-items: center;
  gap: .45rem;
  padding: .2rem .8rem .2rem .25rem;
  border-radius: 999px;
  font-weight: 700;
  color: #fff;
  background: color-mix(in srgb, var(--shard) 75%, #000);
  border: 1px solid color-mix(in srgb, var(--shard) 55%, #fff);
  text-shadow: 0 1px 2px rgba(0, 0, 0, .6);
}
.shards-root .trump-chip i {
  width: 22px; height: 22px;
  border-radius: 50%;
  background-size: cover;
  background-position: center;
  border: 1px solid rgba(0, 0, 0, .55);
}
.shards-root .trump .ttext { font-size: .8rem; color: var(--text-dim); max-width: 520px; }

.shards-root .trick {
  display: flex;
  gap: .9rem;
  justify-content: center;
  align-items: flex-end;
  flex-wrap: wrap;
  min-height: 150px;
  padding: .4rem 0;
}
.shards-root .tcard .who { font-size: .74rem; letter-spacing: .05em; color: var(--text-dim); margin-top: .35rem; }

/* --- cards --- */

.shards-root .card {
  --shard: #888;
  position: relative;
  display: inline-flex;
  flex-direction: column;
  justify-content: space-between;
  width: 86px;
  height: 120px;
  padding: .35rem .45rem .3rem;
  border-radius: 10px;
  border: 1px solid rgba(0, 0, 0, .75);
  color: #fff;
  font-weight: 700;
  text-align: left;
  vertical-align: top;
  cursor: default;
  box-shadow: 0 4px 10px rgba(0, 0, 0, .45);
  text-shadow: 0 1px 2px rgba(0, 0, 0, .85);
  transition: transform .18s ease, box-shadow .18s ease, opacity .18s ease;
}
/* gilt inner frame */
.shards-root .card::after {
  content: '';
  position: absolute;
  inset: 3px;
  border-radius: 7px;
  border: 1px solid rgba(232, 201, 121, .5);
  box-shadow: 0 0 0 1px rgba(0, 0, 0, .3);
  pointer-events: none;
}
.shards-root .card .rank {
  font-family: var(--font-display);
  font-size: 1.55rem;
  font-weight: 700;
  line-height: 1;
}
.shards-root .card .delta { font-size: .8rem; color: var(--gold-bright); font-family: var(--font-body); }
.shards-root .card .shard {
  font-size: .55rem;
  font-weight: 600;
  letter-spacing: .06em;
  text-transform: uppercase;
  opacity: .95;
}
.shards-root .card .ab {
  position: absolute;
  top: .3rem;
  right: .4rem;
  font-size: .85rem;
  color: var(--gold-bright);
  filter: drop-shadow(0 1px 2px #000);
}

.shards-root .card.playable { cursor: pointer; box-shadow: 0 0 0 1px var(--gold-bright), 0 0 10px rgba(232, 201, 121, .28); }
.shards-root .card.playable:hover { transform: translateY(-10px); box-shadow: 0 0 0 2px var(--gold-bright), 0 0 18px rgba(232, 201, 121, .55); }
.shards-root .card.selectable { cursor: pointer; box-shadow: 0 0 0 2px var(--select), 0 0 14px rgba(124, 196, 255, .45); }
.shards-root .card.selectable:hover { transform: translateY(-6px); }
.shards-root .card.selected { box-shadow: 0 0 0 3px var(--select), 0 0 18px rgba(124, 196, 255, .65); transform: translateY(-12px); }
.shards-root .card.dim { opacity: .38; filter: grayscale(.5); }

.shards-root .card.small { width: 46px; height: 64px; padding: .2rem .28rem; border-radius: 6px; }
.shards-root .card.small::after { inset: 2px; border-radius: 4px; }
.shards-root .card.small .rank { font-size: .95rem; }
.shards-root .card.small .shard { display: none; }
.shards-root .card.small .ab { font-size: .6rem; top: auto; bottom: .12rem; right: .22rem; }

.shards-root .card.enter { animation: shards-card-in .38s cubic-bezier(.2, .9, .3, 1.25) backwards; }
@keyframes shards-card-in {
  from { opacity: 0; transform: translateY(16px) scale(.85); }
}

/* --- prompt / action bar --- */

.shards-root .prompt {
  min-height: 2.4rem;
  padding: .55rem .9rem;
  border-radius: 12px;
  border: 1px solid var(--line-dim);
  background: rgba(0, 0, 0, .2);
  font-size: .98rem;
  text-align: center;
  color: var(--text-dim);
}
.shards-root .prompt.mine {
  border-color: var(--gold);
  background: linear-gradient(180deg, rgba(232, 201, 121, .16), rgba(232, 201, 121, .05));
  color: var(--text);
  box-shadow: 0 0 22px rgba(232, 201, 121, .12);
}
.shards-root .prompt .actions { margin-top: .55rem; display: flex; gap: .55rem; justify-content: center; flex-wrap: wrap; }
.shards-root .prompt .revealed { margin: .55rem auto .1rem; display: flex; gap: .5rem; justify-content: center; flex-wrap: wrap; }

/* --- my dock --- */

.shards-root .me {
  padding: .65rem .8rem .8rem;
  border-radius: 14px;
  border: 1px solid var(--line-dim);
  background: linear-gradient(180deg, rgba(255, 255, 255, .04), rgba(0, 0, 0, .12));
}
.shards-root .scoring-strip {
  display: flex;
  gap: .3rem;
  flex-wrap: wrap;
  align-items: center;
  margin-bottom: .55rem;
  min-height: 24px;
}
.shards-root .scoring-strip .label {
  font-family: var(--font-display);
  font-size: .68rem;
  font-weight: 600;
  letter-spacing: .14em;
  text-transform: uppercase;
  color: var(--text-dim);
  margin-right: .35rem;
}
.shards-root .hand-tools {
  display: flex;
  gap: .4rem;
  align-items: center;
  justify-content: flex-end;
}
.shards-root .hand-tools .label {
  font-family: var(--font-display);
  font-size: .68rem;
  font-weight: 600;
  letter-spacing: .14em;
  text-transform: uppercase;
  color: var(--text-dim);
  margin-right: .1rem;
}
.shards-root .hand-tools button { font-size: .68rem; padding: .22rem .6rem; border-radius: 6px; }

.shards-root .hand {
  display: flex;
  gap: .45rem;
  flex-wrap: wrap;
  justify-content: center;
  padding: .6rem .2rem .25rem;
}
.shards-root .hand .card {
  transform: rotate(calc(var(--a, 0) * 1deg)) translateY(calc(var(--y, 0) * 1px));
  /* horizontal touch-drags reorder the hand; vertical still scrolls the page */
  touch-action: pan-y;
  user-select: none;
  -webkit-user-select: none;
}
.shards-root .hand .card.playable:hover, .shards-root .hand .card.selectable:hover { transform: translateY(-12px); }
.shards-root .hand .card.selected { transform: translateY(-14px); }
.shards-root .hand .card.dragging {
  transform: translateY(-8px) scale(1.05);
  opacity: .85;
  cursor: grabbing;
  box-shadow: 0 12px 26px rgba(0, 0, 0, .6), 0 0 0 2px var(--gold-bright);
  z-index: 5;
}

/* --- game log --- */

.shards-root .logbox {
  border: 1px solid var(--line-dim);
  border-radius: 12px;
  background: rgba(0, 0, 0, .25);
  padding: .5rem .75rem .6rem;
  font-size: .82rem;
}
.shards-root .logbox summary {
  cursor: pointer;
  font-family: var(--font-display);
  font-size: .72rem;
  font-weight: 600;
  letter-spacing: .14em;
  text-transform: uppercase;
  color: var(--text-dim);
}
.shards-root .logbox div { max-height: calc(100vh - 190px); overflow-y: auto; margin-top: .4rem; }
.shards-root .logbox p {
  margin: 0;
  padding: .22rem 0;
  color: var(--text-dim);
  border-bottom: 1px solid rgba(255, 255, 255, .05);
}
.shards-root .logbox p:last-child { color: var(--text); border-bottom: 0; }

/* --- game over --- */

.shards-root .gameover {
  position: fixed;
  inset: 0;
  background: radial-gradient(ellipse at center, rgba(11, 16, 31, .82), rgba(11, 16, 31, .95));
  display: flex;
  align-items: center;
  justify-content: center;
  z-index: 10;
}
.shards-root .gameover .panel { max-width: 780px; width: calc(100% - 2rem); max-height: 85vh; overflow: auto; animation: shards-rise .45s cubic-bezier(.2, .9, .3, 1.15) backwards; }
@keyframes shards-rise { from { opacity: 0; transform: translateY(26px) scale(.97); } }
.shards-root .gameover table { border-collapse: collapse; width: 100%; font-size: .92rem; }
.shards-root .gameover th, .shards-root .gameover td { padding: .4rem .55rem; text-align: right; border-bottom: 1px solid rgba(138, 109, 59, .3); }
.shards-root .gameover th {
  font-family: var(--font-display);
  font-size: .7rem;
  letter-spacing: .08em;
  text-transform: uppercase;
  color: var(--ink-dim);
}
.shards-root .gameover th .dot {
  display: inline-block;
  width: 9px; height: 9px;
  border-radius: 50%;
  margin-right: .3rem;
  vertical-align: baseline;
}
.shards-root .gameover th:first-child, .shards-root .gameover td:first-child { text-align: left; }
.shards-root .gameover td small { color: var(--ink-dim); }
.shards-root .gameover .winner td { font-weight: 700; background: rgba(201, 169, 89, .14); }

/* --- tooltip & toast --- */

.shards-root .tipbox {
  position: fixed;
  z-index: 30;
  max-width: 260px;
  padding: .45rem .65rem;
  border-radius: 8px;
  border: 1px solid var(--gold);
  background: var(--night-0);
  color: var(--text);
  font-size: .8rem;
  font-weight: 400;
  line-height: 1.35;
  box-shadow: 0 6px 24px rgba(0, 0, 0, .55);
  pointer-events: none;
}
.shards-root .tipbox.hidden { display: none; }

.shards-root .toast {
  position: fixed;
  bottom: 1.2rem;
  left: 50%;
  transform: translateX(-50%);
  background: #7e2f2f;
  border: 1px solid #c96a5a;
  color: #fff;
  padding: .55rem 1.1rem;
  border-radius: 10px;
  font-size: .92rem;
  z-index: 20;
  box-shadow: 0 6px 24px rgba(0, 0, 0, .5);
  animation: shards-fadeout 4s forwards;
}
@keyframes shards-fadeout { 0%, 78% { opacity: 1; } 100% { opacity: 0; } }

@media (max-height: 820px) {
  .shards-root .table-wrap { min-height: 0; gap: .6rem; }
  .shards-root .table-center { padding: .6rem; }
  .shards-root .trick { min-height: 110px; }
}

/* --- small screens --- */

@media (max-width: 1000px) {
  .shards-root .screen-game { grid-template-columns: 1fr; }
  .shards-root .side { position: static; }
  .shards-root .logbox div { max-height: 160px; }
  .shards-root .table-wrap { min-height: 0; }
  .shards-root .trick { min-height: 120px; }
}

@media (max-width: 640px) {
  .shards-root .screen { padding: .9rem .6rem 1.5rem; }
  .shards-root .panel { padding: 1rem 1.1rem 1.2rem; }
  .shards-root .card { width: 66px; height: 92px; }
  .shards-root .card .rank { font-size: 1.2rem; }
  .shards-root .card.small { width: 40px; height: 56px; }
  .shards-root .opp { min-width: 150px; padding: .45rem .6rem .5rem; }
  .shards-root .round-info { gap: .8rem; }
  .shards-root .hand { gap: .3rem; }
  /* the arc fan makes wrapped hand rows collide on narrow screens */
  .shards-root .hand .card { transform: none; }
}
`;

// ─── WEBSOCKET HOOK ────────────────────────────────────────────────

function useGameConnection() {
  const [connected, setConnected] = useState(false);
  const [roomCode, setRoomCode] = useState(null);
  const [playerId, setPlayerId] = useState(null);
  const [isHost, setIsHost] = useState(false);
  const [lobby, setLobby] = useState([]);
  const [gameStarted, setGameStarted] = useState(false);
  const [spectating, setSpectating] = useState(false);
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
          setLobby(msg.players || []);
          if (msg.game_started !== undefined) setGameStarted(msg.game_started);
          break;
        case "game_started":
          setGameStarted(true);
          break;
        case "spectating":
          setRoomCode(msg.room_code);
          setSpectating(true);
          if (msg.token) tokenRef.current = msg.token;
          break;
        case "game_state":
          setGameState(msg.state);
          setPhaseInfo(msg.phase_info);
          setYourTurn(msg.your_turn);
          setWaitingFor(msg.waiting_for || []);
          break;
        case "game_log":
          setGameLogs((prev) => [...prev, ...(msg.messages || [])]);
          break;
        case "game_over":
          setGameOver(true);
          break;
        case "action_error":
        case "error":
          setError(msg.message);
          setTimeout(() => setError(null), 4200);
          break;
        default:
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
        wsRef.current?.send(JSON.stringify({ type: "create", game: "shards", name }));
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

  const spectateRoom = useCallback((code) => {
    connect(() => {
      setTimeout(() => {
        wsRef.current?.send(JSON.stringify({ type: "spectate", room_code: code.toUpperCase() }));
      }, 100);
    });
  }, [connect]);

  // Auto-create/join/spectate from the main menu (intent left in sessionStorage)
  useEffect(() => {
    if (tokenRef.current) return;
    const pending = sessionStorage.getItem("pending_action");
    const pendingSpectate = sessionStorage.getItem("pending_spectate");
    if (pending) {
      try {
        const { roomCode: code, playerName } = JSON.parse(pending);
        sessionStorage.removeItem("pending_action");
        if (code) joinRoom(code, playerName);
        else createRoom(playerName);
      } catch {
        sessionStorage.removeItem("pending_action");
      }
    } else if (pendingSpectate) {
      try {
        const { roomCode: code } = JSON.parse(pendingSpectate);
        sessionStorage.removeItem("pending_spectate");
        if (code) spectateRoom(code);
      } catch {
        sessionStorage.removeItem("pending_spectate");
      }
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const startGame = useCallback(() => send({ type: "start" }), [send]);
  const sendAction = useCallback((action) => send({ type: "action", action }), [send]);

  return {
    connected, roomCode, playerId, isHost, lobby,
    gameStarted, spectating, gameState, phaseInfo,
    yourTurn, waitingFor, gameLogs, gameOver, error,
    createRoom, joinRoom, spectateRoom, startGame, sendAction,
  };
}

// ─── CARD & CHIP COMPONENTS ────────────────────────────────────────

function ShardCard({ card, small, effectiveRank, extraClasses = [], onClick, enter, dealDelay, style, ...rest }) {
  const s = shardOf(card.shard);
  const eff = effectiveRank ?? null;
  const delta = eff !== null && eff !== card.rank ? eff : null;
  const tip = card.ability && s.abilities[card.ability]
    ? `${s.name} ✦ — ${s.abilities[card.ability].text}`
    : undefined;
  const cls = ["card", small ? "small" : null, enter ? "enter" : null, ...extraClasses]
    .filter(Boolean).join(" ");
  return (
    <div
      className={cls}
      data-tip={tip}
      onClick={onClick}
      style={{
        // Shard art behind a dark scrim so the white rank/name stay readable;
        // the shard color shows through as border and as fallback while art loads.
        background:
          `linear-gradient(180deg, rgba(0,0,0,.55), rgba(0,0,0,.12) 38%, rgba(0,0,0,.12) 62%, rgba(0,0,0,.55)),`
          + ` url('${artUrl(card.shard)}') center / cover ${s.color}`,
        "--shard": s.color,
        ...(enter && dealDelay ? { animationDelay: `${dealDelay}ms` } : {}),
        ...(style || {}),
      }}
      {...rest}
    >
      <span className="rank">
        {card.rank}
        {delta !== null && <span className="delta"> →{delta}</span>}
      </span>
      {card.ability && <span className="ab">✦</span>}
      <span className="shard">{s.name}</span>
    </div>
  );
}

// Glanceable summary of a scoring area: one chip per shard with a count,
// plus a chip totting up scored ability cards.
function Chips({ area }) {
  const byShard = new Map();
  let abilities = 0;
  for (const c of area) {
    byShard.set(c.shard, (byShard.get(c.shard) ?? 0) + 1);
    if (c.ability) abilities++;
  }
  return (
    <div className="chips">
      {[...byShard.entries()].map(([sid, n]) => {
        const s = shardOf(sid);
        return (
          <span key={sid} className="chip" style={{ "--shard": s.color }}
            data-tip={`${n} ${s.name} card${n > 1 ? "s" : ""} scored`}>
            <i style={{ backgroundImage: `url('${artUrl(sid)}')` }} />
            {n}
          </span>
        );
      })}
      {abilities > 0 && (
        <span className="chip ab-chip" data-tip={`${abilities} ability card${abilities > 1 ? "s" : ""} scored`}>
          ✦ {abilities}
        </span>
      )}
    </div>
  );
}

function Avatars({ names }) {
  return (
    <span className="avatars">
      {names.map((n, i) => (
        <i key={i} title={n}>{(n || "?").trim().charAt(0).toUpperCase() || "?"}</i>
      ))}
    </span>
  );
}

// ─── LOBBY ─────────────────────────────────────────────────────────

function LobbyScreen({ conn }) {
  const [mode, setMode] = useState(null); // null | "create" | "join"
  const [name, setName] = useState("");
  const [joinCode, setJoinCode] = useState("");

  if (!conn.roomCode) {
    return (
      <section className="screen">
        <div className="panel">
          <h2>Shards of Creation</h2>
          <p className="hint">Cosmere trick-taking for 2–4 players — win tricks, forge shards.</p>
          {!mode && (
            <div className="row">
              <button className="primary" onClick={() => setMode("create")}>Create room</button>
              <button onClick={() => setMode("join")}>Join by code</button>
            </div>
          )}
          {mode === "create" && (
            <>
              <h3>Create room</h3>
              <div className="field">
                <label>Your name</label>
                <input value={name} autoFocus onChange={(e) => setName(e.target.value)}
                  onKeyDown={(e) => e.key === "Enter" && name.trim() && conn.createRoom(name.trim())} />
              </div>
              <div className="row">
                <button className="primary" disabled={!name.trim()}
                  onClick={() => name.trim() && conn.createRoom(name.trim())}>Create</button>
                <button onClick={() => setMode(null)}>Back</button>
              </div>
            </>
          )}
          {mode === "join" && (
            <>
              <h3>Join room</h3>
              <div className="field">
                <label>Room code</label>
                <input className="code" value={joinCode} maxLength={5} autoFocus
                  onChange={(e) => setJoinCode(e.target.value.toUpperCase())} />
              </div>
              <div className="field">
                <label>Your name</label>
                <input value={name} onChange={(e) => setName(e.target.value)}
                  onKeyDown={(e) => e.key === "Enter" && name.trim() && joinCode.length >= 4
                    && conn.joinRoom(joinCode, name.trim())} />
              </div>
              <div className="row">
                <button className="primary" disabled={!name.trim() || joinCode.length < 4}
                  onClick={() => conn.joinRoom(joinCode, name.trim())}>Join</button>
                <button onClick={() => setMode(null)}>Back</button>
              </div>
            </>
          )}
        </div>
      </section>
    );
  }

  // Waiting room
  return (
    <section className="screen">
      <div className="panel">
        <h2>Game lobby</h2>
        <p className="hint">Share this code with friends. Shards are drawn at random when the game starts.</p>
        <div className="room-code">{conn.roomCode}</div>
        <h3>Players</h3>
        <ul className="lobby-players">
          {conn.lobby.map((p, i) => (
            <li key={p.player_id ?? i}>
              <Avatars names={[p.name]} />
              {p.name}{p.player_id === conn.playerId ? " (you)" : ""}
              {i === 0 && <span className="host-tag">host</span>}
              {p.connected === false && <span className="offline">offline</span>}
            </li>
          ))}
        </ul>
        <div className="row">
          {conn.isHost
            ? <button className="primary" disabled={conn.lobby.length < 2} onClick={conn.startGame}>
                Start game
              </button>
            : <span style={{ color: "var(--ink-dim)", fontStyle: "italic", alignSelf: "center" }}>
                Waiting for the host to start…
              </span>}
        </div>
      </div>
    </section>
  );
}

// ─── GAME TABLE ────────────────────────────────────────────────────

function promptText(view, selected) {
  const p = view.pending;
  const myTurn = view.turn === view.you && view.you != null;
  if (view.phase === "roundStartDiscard") {
    const meWaiting = p?.waitingOn?.includes(view.you);
    return meWaiting
      ? `Cultivation trump: choose ${p.count} card${p.count > 1 ? "s" : ""} to discard, then confirm.`
      : `Waiting for ${(p?.waitingOn ?? []).map((i) => view.players[i].name).join(", ")} to discard…`;
  }
  if (view.phase === "roundStartPlace") {
    const meWaiting = p?.waitingOn?.includes(view.you);
    return meWaiting
      ? "Devotion trump: click a card to place in your scoring area now."
      : `Waiting for ${(p?.waitingOn ?? []).map((i) => view.players[i].name).join(", ")} to place a card…`;
  }
  if (view.phase === "odiumSteal") {
    return myTurn
      ? "Odium trump: you have the fewest scoring cards — click a card in the biggest scoring area to steal it, or decline."
      : `${view.players[view.turn]?.name} may steal a scoring-area card…`;
  }
  if (view.phase === "ability") {
    const who = p?.player != null ? view.players[p.player]?.name : null;
    if (!myTurn) return `${who} is resolving an ability…`;
    switch (p?.type) {
      case "autonomy_discard_draw": return "You may discard an Autonomy card from your hand to draw a card.";
      case "cultivation_discard_draw": return "Discard up to two cards, then draw that many.";
      case "preservation_discard": return "You drew a card — now choose a card to discard.";
      case "cultivation_reveal_add": return "Add the revealed card's rank to your card, or put it back?";
      case "ruin_reveal_subtract": return p.targets?.length
        ? "Click another player's card to subtract the revealed rank from it, or put the card back."
        : "No valid target — put the revealed card back.";
      case "devotion_exchange": return "You may exchange this card with one in your scoring area (click it) — its effect will activate.";
      case "dominion_discard_lowest": return "Tie for lowest rank — click which card to discard.";
      case "odium_discard_take": return selected.size
        ? "Now click an opponent below to take a random card from their hand."
        : "Click a card in your hand to discard, then pick an opponent to steal from.";
      default: return "Resolving an ability…";
    }
  }
  if (view.phase === "award") {
    const extra = view.awardOptions?.discard?.length
      ? " (or take a Preservation card from the discard below)"
      : "";
    return myTurn
      ? `You won the trick! Click a card to add it to your scoring area${extra}.`
      : `${view.players[view.turn]?.name} won the trick and is choosing a card…`;
  }
  if (view.phase === "play") {
    return myTurn ? "Your turn — play a card." : `Waiting for ${view.players[view.turn]?.name}…`;
  }
  return "";
}

function Prompt({ view, selected, sendAction, trackEnter, toast }) {
  const p = view.pending;
  const myTurn = view.turn === view.you && view.you != null;
  const meActing = !view.result
    && (myTurn || (p?.waitingOn?.includes(view.you) ?? false));

  const buttons = [];
  const btn = (label, onclick, primary = false) => {
    buttons.push(
      <button key={buttons.length} className={primary ? "primary" : undefined} onClick={onclick}>
        {label}
      </button>
    );
  };

  if (myTurn || ["roundStartDiscard", "roundStartPlace"].includes(view.phase)) {
    if (view.phase === "ability" && myTurn) {
      switch (p?.type) {
        case "autonomy_discard_draw":
          btn("Decline", () => sendAction({ type: "abilityChoice", cardId: null }));
          break;
        case "cultivation_discard_draw":
          btn(`Discard ${selected.size} & draw ${selected.size}`, () =>
            sendAction({ type: "abilityChoice", cardIds: [...selected] }), true);
          break;
        case "cultivation_reveal_add":
          btn(`Add +${p.revealed?.rank} to your card`, () =>
            sendAction({ type: "abilityChoice", take: true }), true);
          btn("Put it back", () => sendAction({ type: "abilityChoice", take: false }));
          break;
        case "ruin_reveal_subtract":
          btn("Put it back", () => sendAction({ type: "abilityChoice", targetCardId: null }));
          break;
        case "devotion_exchange":
          btn("Decline", () => sendAction({ type: "abilityChoice", targetCardId: null }));
          break;
        case "odium_discard_take":
          for (const v of p.victims ?? []) {
            btn(`Take from ${view.players[v]?.name}`, () => {
              const cardId = [...selected][0];
              if (!cardId) return toast("First click a card in your hand to discard.");
              sendAction({ type: "abilityChoice", cardId, targetPlayer: v });
            }, true);
          }
          break;
        default:
          break;
      }
    }
    if (view.phase === "odiumSteal" && myTurn) {
      btn("Decline the steal", () => sendAction({ type: "odiumSteal", cardId: null }));
    }
    if (view.phase === "roundStartDiscard" && p?.waitingOn?.includes(view.you)) {
      btn(`Discard ${selected.size}/${p.count}`, () => {
        if (selected.size !== p.count) return toast(`Select exactly ${p.count} cards.`);
        sendAction({ type: "roundStartDiscard", cardIds: [...selected] });
      }, true);
    }
  }

  return (
    <div className={"prompt" + (meActing ? " mine" : "")}>
      <div>{promptText(view, selected)}</div>
      {p?.revealed && view.phase === "ability" && (
        <div className="revealed">
          <ShardCard card={p.revealed} enter={trackEnter(p.revealed.id)} />
        </div>
      )}
      {/* Preservation trump: offer discard-pile cards to the trick winner */}
      {view.phase === "award" && myTurn && view.awardOptions?.discard?.length > 0 && (
        <div className="revealed">
          {view.awardOptions.discard.map((c) => (
            <ShardCard key={c.id} card={c} extraClasses={["selectable"]}
              onClick={() => sendAction({ type: "chooseTrickCard", cardId: c.id })} />
          ))}
        </div>
      )}
      {buttons.length > 0 && <div className="actions">{buttons}</div>}
    </div>
  );
}

function GameOverPanel({ view }) {
  const { scores, winners } = view.result;
  const shardCols = view.shardIds.map((sid) => shardOf(sid));
  return (
    <div className="gameover">
      <div className="panel">
        <h2>
          {winners.length > 1 ? "Shared victory!" : `${view.players[winners[0]]?.name} wins!`}
        </h2>
        <table>
          <thead>
            <tr>
              <th>Player</th>
              {shardCols.map((s) => (
                <th key={s.id}><span className="dot" style={{ background: s.color }} />{s.name}</th>
              ))}
              <th>Abilities</th>
              <th>Resonance</th>
              <th>Total</th>
            </tr>
          </thead>
          <tbody>
            {scores.map((sc) => {
              const isWinner = winners.includes(sc.player);
              return (
                <tr key={sc.player} className={isWinner ? "winner" : undefined}>
                  <td>{sc.name}{isWinner ? " 👑" : ""}</td>
                  {view.shardIds.map((sid) => (
                    <td key={sid}>{sc.shardPoints[sid]}<br /><small>×{sc.byShard[sid]}</small></td>
                  ))}
                  <td>{sc.abilityPoints}</td>
                  <td>{sc.resonance}<br /><small>×{sc.resonanceSets}</small></td>
                  <td><strong>{sc.total}</strong></td>
                </tr>
              );
            })}
          </tbody>
        </table>
        <div className="row">
          <span style={{ color: "var(--ink-dim)", fontSize: ".9rem", fontStyle: "italic" }}>
            Use “← Games” (top left) to return to the menu.
          </span>
        </div>
      </div>
    </div>
  );
}

function GameScreen({ conn }) {
  const view = conn.gameState;
  const sendAction = conn.sendAction;

  // local UI state: multi-select for discard prompts, hand display order,
  // drag-in-progress, transient toasts
  const [selected, setSelected] = useState(() => new Set());
  const [handOrder, setHandOrder] = useState([]);
  const [draggingId, setDraggingId] = useState(null);
  const [notice, setNotice] = useState(null);

  const handRef = useRef(null);
  const logRef = useRef(null);
  const seenRef = useRef(new Set()); // card ids rendered once (entry animations only for new cards)
  const suppressClickRef = useRef(false); // a real drag swallows the click that would play/select
  const noticeTimer = useRef(null);

  // every server update clears the multi-select, like the old client
  useEffect(() => { setSelected(new Set()); }, [view]);

  // pin the log to its newest entry
  useEffect(() => {
    if (logRef.current) logRef.current.scrollTop = logRef.current.scrollHeight;
  }, [view.log?.length]);

  const myTurn = view.turn === view.you && view.you != null;
  const isSpectator = view.you == null;
  const meActing = !view.result
    && (myTurn || (view.pending?.waitingOn?.includes(view.you) ?? false));

  // flag the tab title whenever we owe the game a decision
  useEffect(() => {
    document.title = meActing ? "● Your turn — Shards of Creation" : "Shards of Creation";
    return () => { document.title = "Board Game Engine"; };
  }, [meActing]);

  const toast = useCallback((text) => {
    clearTimeout(noticeTimer.current);
    setNotice(text);
    noticeTimer.current = setTimeout(() => setNotice(null), 4200);
  }, []);

  // Entry animation bookkeeping: true only the first time a card id is rendered.
  const trackEnter = useCallback((id) => {
    if (seenRef.current.has(id)) return false;
    seenRef.current.add(id);
    return true;
  }, []);

  const seatName = (i) => view.players[i]?.name ?? "?";

  // Apply our display order to the server's hand. New card ids (draws) keep the
  // server's relative order and go to the end; departed ids fall away naturally.
  const orderedHand = useMemo(() => {
    const pos = new Map(handOrder.map((id, i) => [id, i]));
    return [...(view.hand ?? [])].sort((a, b) => (pos.get(a.id) ?? 1e9) - (pos.get(b.id) ?? 1e9));
  }, [view.hand, handOrder]);

  const autoSortHand = (by) => {
    const si = new Map((view.shardIds ?? []).map((s, i) => [s, i]));
    const idx = (c) => si.get(c.shard) ?? 99;
    setHandOrder([...(view.hand ?? [])].sort((a, b) => by === "color"
      ? idx(a) - idx(b) || a.rank - b.rank
      : a.rank - b.rank || idx(a) - idx(b)
    ).map((c) => c.id));
  };

  // Drag-to-reorder: the pressed card hops between slots as the pointer moves
  // (pointer events, so mouse and touch both work).
  const onHandPointerDown = (e, cardId) => {
    if (e.pointerType === "mouse" && e.button !== 0) return;
    const startX = e.clientX, startY = e.clientY;
    let dragging = false;
    const move = (ev) => {
      const handEl = handRef.current;
      if (!handEl) return up();
      if (!dragging) {
        if (Math.hypot(ev.clientX - startX, ev.clientY - startY) < 8) return;
        dragging = true;
        setDraggingId(cardId);
      }
      const children = [...handEl.children].filter((o) => o.dataset?.cid);
      const next = children.find((o) => {
        if (o.dataset.cid === cardId) return false;
        const r = o.getBoundingClientRect();
        return ev.clientY < r.top || (ev.clientY < r.bottom && ev.clientX < r.left + r.width / 2);
      });
      const displayed = children.map((o) => o.dataset.cid);
      const ids = displayed.filter((id) => id !== cardId);
      const insertAt = next ? ids.indexOf(next.dataset.cid) : ids.length;
      ids.splice(insertAt, 0, cardId);
      if (ids.join("\u0000") !== displayed.join("\u0000")) setHandOrder(ids);
    };
    const up = () => {
      window.removeEventListener("pointermove", move);
      window.removeEventListener("pointerup", up);
      window.removeEventListener("pointercancel", up);
      if (!dragging) return;
      setDraggingId(null);
      suppressClickRef.current = true;
      setTimeout(() => { suppressClickRef.current = false; }, 0);
    };
    window.addEventListener("pointermove", move);
    window.addEventListener("pointerup", up);
    window.addEventListener("pointercancel", up);
  };

  const guarded = (fn) => fn ? () => { if (!suppressClickRef.current) fn(); } : undefined;

  // Per-card interactivity in my hand, straight port of the old renderHand()
  const handCardProps = (card) => {
    const p = view.pending;
    const classes = [];
    let onClick = null;
    if (view.phase === "play" && myTurn) {
      if ((view.legal ?? []).includes(card.id)) {
        classes.push("playable");
        onClick = () => sendAction({ type: "playCard", cardId: card.id });
      } else {
        classes.push("dim");
      }
    } else if (view.phase === "ability" && myTurn) {
      if (p?.type === "autonomy_discard_draw" && p.targets?.includes(card.id)) {
        classes.push("selectable");
        onClick = () => sendAction({ type: "abilityChoice", cardId: card.id });
      } else if (p?.type === "preservation_discard") {
        classes.push("selectable");
        onClick = () => sendAction({ type: "abilityChoice", cardId: card.id });
      } else if (p?.type === "dominion_discard_lowest" && p.targets?.includes(card.id)) {
        classes.push("selectable");
        onClick = () => sendAction({ type: "abilityChoice", cardId: card.id });
      } else if (p?.type === "odium_discard_take") {
        classes.push(selected.has(card.id) ? "selected" : "selectable");
        onClick = () => setSelected(new Set([card.id]));
      } else if (p?.type === "cultivation_discard_draw") {
        classes.push(selected.has(card.id) ? "selected" : "selectable");
        onClick = () => setSelected((prev) => {
          const next = new Set(prev);
          if (next.has(card.id)) next.delete(card.id);
          else if (next.size < 2) next.add(card.id);
          return next;
        });
      }
    } else if (view.phase === "roundStartPlace" && p?.waitingOn?.includes(view.you)) {
      classes.push("selectable");
      onClick = () => sendAction({ type: "roundStartPlace", cardId: card.id });
    } else if (view.phase === "roundStartDiscard" && p?.waitingOn?.includes(view.you)) {
      classes.push(selected.has(card.id) ? "selected" : "selectable");
      onClick = () => setSelected((prev) => {
        const next = new Set(prev);
        if (next.has(card.id)) next.delete(card.id);
        else if (next.size < (p.count ?? 1)) next.add(card.id);
        return next;
      });
    }
    return { classes, onClick };
  };

  const stealMode = myTurn && view.phase === "odiumSteal";
  const trump = view.trumpShard ? shardOf(view.trumpShard) : null;
  const n = orderedHand.length;
  let dealt = 0;

  return (
    <section className="screen-game">
      <div className="table-wrap">
        {/* opponents (and, for spectators, everyone) */}
        <div className="opponents">
          {view.players.map((p, i) => {
            if (!isSpectator && i === view.you) return null;
            return (
              <div key={i} className={"opp" + (view.turn === i ? " turn" : "")}>
                <div className="seat-head">
                  <span className="name">{p.name}</span>
                  {view.leader === i && <span className="lead" data-tip="Led this trick">lead</span>}
                  <span className="handcount" data-tip="Cards in hand">
                    <i className="cardback" />{p.handCount}
                  </span>
                </div>
                <Chips area={p.scoringArea} />
                <div className="scoring-strip">
                  {p.scoringArea.map((c) => {
                    const stealable = stealMode && view.pending?.targets?.includes(c.id);
                    return (
                      <ShardCard key={c.id} card={c} small
                        extraClasses={stealable ? ["selectable"] : []}
                        onClick={stealable
                          ? () => sendAction({ type: "odiumSteal", cardId: c.id })
                          : undefined} />
                    );
                  })}
                </div>
              </div>
            );
          })}
        </div>

        {/* center */}
        <div className="table-center">
          <div className="round-info">
            <span className="stat">Round <b>{view.round}</b> / 3</span>
            <span className="stat">Trick <b>{view.trickNum}</b></span>
            <span className="stat">Draw <b>{view.drawCount}</b></span>
            <span className="stat">Discard <b>{view.discardCount}</b></span>
            {isSpectator && <span className="stat">spectating</span>}
          </div>
          <div className="trump">
            {trump && (
              <>
                <span className="trump-line">
                  <span className="trump-label">Trump</span>
                  <span className="trump-chip" style={{ "--shard": trump.color }}>
                    <i style={{ backgroundImage: `url('${artUrl(view.trumpShard)}')` }} />
                    {trump.name}
                  </span>
                </span>
                {trump.trumpText && trump.trumpText !== "No trump ability." && (
                  <span className="ttext">{trump.trumpText}</span>
                )}
              </>
            )}
          </div>
          <div className="trick">
            {view.trick.map((entry) => {
              const clickable =
                (myTurn && view.phase === "award" && view.awardOptions?.played?.includes(entry.card.id))
                || (myTurn && view.phase === "ability" && view.pending?.type === "ruin_reveal_subtract"
                    && view.pending.targets?.includes(entry.card.id));
              return (
                <div key={entry.card.id} className="tcard">
                  <ShardCard card={entry.card}
                    effectiveRank={entry.effectiveRank}
                    enter={trackEnter(entry.card.id)}
                    extraClasses={clickable ? ["selectable"] : []}
                    onClick={clickable ? () => {
                      if (view.phase === "award") sendAction({ type: "chooseTrickCard", cardId: entry.card.id });
                      else sendAction({ type: "abilityChoice", targetCardId: entry.card.id });
                    } : undefined} />
                  <div className="who">{seatName(entry.player)}</div>
                </div>
              );
            })}
          </div>
        </div>

        <Prompt view={view} selected={selected} sendAction={sendAction}
          trackEnter={trackEnter} toast={toast} />

        {/* my dock */}
        <div className="me">
          <div className="scoring-strip">
            <span className="label">Your scoring area</span>
            {!isSpectator && (
              <>
                <Chips area={view.players[view.you].scoringArea} />
                {view.players[view.you].scoringArea.map((c) => {
                  const exchangeMode = myTurn && view.phase === "ability"
                    && view.pending?.type === "devotion_exchange";
                  const targetable = exchangeMode && view.pending.targets?.includes(c.id);
                  return (
                    <ShardCard key={c.id} card={c} small
                      extraClasses={targetable ? ["selectable"] : []}
                      onClick={targetable
                        ? () => sendAction({ type: "abilityChoice", targetCardId: c.id })
                        : undefined} />
                  );
                })}
              </>
            )}
          </div>
          {!isSpectator && orderedHand.length >= 2 && (
            <div className="hand-tools">
              <span className="label">Sort</span>
              <button type="button" data-tip="Group by shard, then rank"
                onClick={() => autoSortHand("color")}>By color</button>
              <button type="button" data-tip="Low to high rank"
                onClick={() => autoSortHand("rank")}>By rank</button>
            </div>
          )}
          <div className="hand" ref={handRef}>
            {!isSpectator && orderedHand.map((card, i) => {
              const { classes, onClick } = handCardProps(card);
              const a = (i - (n - 1) / 2) * Math.min(3, 24 / Math.max(n, 1));
              const enter = trackEnter(card.id);
              const delay = enter ? (dealt++) * 45 : 0;
              if (draggingId === card.id) classes.push("dragging");
              return (
                <ShardCard key={card.id} card={card}
                  extraClasses={classes}
                  enter={enter} dealDelay={delay}
                  onClick={guarded(onClick)}
                  data-cid={card.id}
                  onPointerDown={(e) => onHandPointerDown(e, card.id)}
                  style={{ "--a": a.toFixed(2), "--y": (Math.abs(a) * 1.5).toFixed(2) }} />
              );
            })}
          </div>
        </div>
      </div>

      {/* log sidebar */}
      <aside className="side">
        <details className="logbox" open>
          <summary>Game log</summary>
          <div ref={logRef}>
            {(view.log ?? []).map((l, i) => <p key={i}>{l.msg ?? l}</p>)}
          </div>
        </details>
      </aside>

      {view.result && <GameOverPanel view={view} />}
      {notice && <div className="toast">{notice}</div>}
    </section>
  );
}

// ─── MAIN APP ──────────────────────────────────────────────────────

export default function App() {
  const conn = useGameConnection();
  const view = conn.gameState;
  const rootRef = useRef(null);
  const tipRef = useRef(null);

  // Single floating tooltip, shared by everything with a data-tip attribute.
  useEffect(() => {
    const root = rootRef.current;
    const tip = tipRef.current;
    if (!root || !tip) return;
    const over = (e) => {
      const t = e.target.closest?.("[data-tip]");
      if (!t || !root.contains(t)) { tip.classList.add("hidden"); return; }
      tip.textContent = t.dataset.tip;
      tip.classList.remove("hidden");
      const r = t.getBoundingClientRect();
      const w = tip.offsetWidth, h = tip.offsetHeight;
      tip.style.left = `${Math.max(6, Math.min(r.left + r.width / 2 - w / 2, window.innerWidth - w - 6))}px`;
      tip.style.top = `${r.top - h - 8 < 6 ? r.bottom + 8 : r.top - h - 8}px`;
    };
    document.addEventListener("mouseover", over);
    return () => document.removeEventListener("mouseover", over);
  }, []);

  const inGame = (conn.gameStarted || conn.spectating) && view;
  const waitingForState = (conn.gameStarted || conn.spectating) && !view;

  return (
    <div className="shards-root" ref={rootRef}>
      <style>{CSS}</style>
      <header className="sh-header">
        <h1>Shards of Creation</h1>
        <span className={"conn" + (conn.connected ? " ok" : "")} title="connection">
          <i /><span className="conn-label">{conn.roomCode ? "reconnecting…" : "offline"}</span>
        </span>
      </header>

      {/* game_started can arrive before the first game_state — hold a beat */}
      {waitingForState && (
        <section className="screen">
          <div className="panel"><h2>Dealing the cards…</h2></div>
        </section>
      )}
      {!inGame && !waitingForState && <LobbyScreen conn={conn} />}
      {inGame && <GameScreen conn={conn} />}

      <div className="tipbox hidden" ref={tipRef} />
      {conn.error && <div className="toast">{conn.error}</div>}
    </div>
  );
}
