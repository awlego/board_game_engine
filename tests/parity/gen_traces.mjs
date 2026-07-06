// Shards of Creation — cross-language parity trace generator.
//
// Runs full games against the ORIGINAL JavaScript engine
// (overnightlemons.com server/games/shards/engine.js), choosing a
// pseudo-random legal action at every step with a seeded driver LCG, and
// records the chosen action plus the full state after every action.
// The Python port replays these traces in tests/parity/test_parity.py and
// must reproduce every state byte-for-byte (canonical JSON, sorted keys).
//
// The enumerateActions() function here MUST mirror
// server/shards/engine.py::enumerate_actions exactly (same actions, same
// order) — the traces record the index chosen so the replayer verifies the
// enumeration itself, not just the applied action.
//
// Regenerate traces with:
//   node tests/parity/gen_traces.mjs
//
// NOTE: the JS engine was RETIRED from overnightlemons.com on 2026-07-06
// (removed in its commit 4232012) after the port was play-verified; the
// committed traces are now the frozen ground truth. To regenerate, check
// out overnightlemons.com at 4a99a11 or earlier so the path below exists.

import { gzipSync } from 'node:zlib';
import { writeFileSync, mkdirSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';

const ENGINE_URL = 'file:///Users/awlego/Repositories/overnightlemons.com/server/games/shards/engine.js';
const { createGame, applyAction, legalPlays, awardOptions, viewFor } = await import(ENGINE_URL);

const OUT_DIR = join(dirname(fileURLToPath(import.meta.url)), 'traces');
mkdirSync(OUT_DIR, { recursive: true });

// --- driver RNG: 32-bit LCG (state always < 2^32, so exact in float64) ---
function lcg(seed) {
  let s = seed >>> 0;
  return () => {
    s = (s * 1664525 + 1013904223) >>> 0;
    return s / 4294967296;
  };
}

// k-combinations in itertools.combinations order (lexicographic by index)
function combinations(arr, k) {
  const out = [];
  const rec = (start, acc) => {
    if (acc.length === k) { out.push([...acc]); return; }
    for (let i = start; i <= arr.length - (k - acc.length); i++) {
      acc.push(arr[i]);
      rec(i + 1, acc);
      acc.pop();
    }
  };
  rec(0, []);
  return out;
}

// Mirror of server/shards/engine.py::enumerate_actions — keep in sync.
function enumerateActions(state, player) {
  const phase = state.phase;
  if (phase === 'play') {
    if (state.turn !== player) return [];
    return legalPlays(state, player).map((cid) => ({ type: 'playCard', cardId: cid }));
  }
  if (phase === 'ability') {
    if (state.turn !== player) return [];
    const p = state.pending;
    const hand = state.players[player].hand;
    switch (p.type) {
      case 'autonomy_discard_draw':
        return [...p.targets.map((cid) => ({ type: 'abilityChoice', cardId: cid })),
                { type: 'abilityChoice', cardId: null }];
      case 'cultivation_discard_draw': {
        const avail = state.drawDeck.length + state.discard.length;
        const acts = [{ type: 'abilityChoice', cardIds: [] }];
        if (avail >= 1) acts.push(...hand.map((cid) => ({ type: 'abilityChoice', cardIds: [cid] })));
        if (avail >= 2) acts.push(...combinations(hand, 2).map((ids) => ({ type: 'abilityChoice', cardIds: ids })));
        return acts;
      }
      case 'preservation_discard':
        return hand.map((cid) => ({ type: 'abilityChoice', cardId: cid }));
      case 'cultivation_reveal_add':
        return [{ type: 'abilityChoice', take: true }, { type: 'abilityChoice', take: false }];
      case 'ruin_reveal_subtract':
        return [...p.targets.map((cid) => ({ type: 'abilityChoice', targetCardId: cid })),
                { type: 'abilityChoice', targetCardId: null }];
      case 'devotion_exchange':
        return [...p.targets.map((cid) => ({ type: 'abilityChoice', targetCardId: cid })),
                { type: 'abilityChoice', targetCardId: null }];
      case 'dominion_discard_lowest':
        return p.targets.map((cid) => ({ type: 'abilityChoice', cardId: cid }));
      case 'odium_discard_take':
        return hand.flatMap((cid) => p.victims.map((v) => ({ type: 'abilityChoice', cardId: cid, targetPlayer: v })));
      default:
        return [];
    }
  }
  if (phase === 'award') {
    if (state.turn !== player) return [];
    const o = awardOptions(state);
    return [...o.played, ...o.discard].map((cid) => ({ type: 'chooseTrickCard', cardId: cid }));
  }
  if (phase === 'roundStartDiscard') {
    if (state.pending.selections[player]) return [];
    return combinations(state.players[player].hand, state.pending.count)
      .map((ids) => ({ type: 'roundStartDiscard', cardIds: ids }));
  }
  if (phase === 'roundStartPlace') {
    if (state.pending.selections[player] != null) return [];
    return state.players[player].hand.map((cid) => ({ type: 'roundStartPlace', cardId: cid }));
  }
  if (phase === 'odiumSteal') {
    if (state.turn !== player) return [];
    return [...state.pending.targets.map((cid) => ({ type: 'odiumSteal', cardId: cid })),
            { type: 'odiumSteal', cardId: null }];
  }
  return []; // gameOver / setup
}

// Simultaneous phases have turn === null; the driver acts as the first
// still-waiting seat. Mirrored by the replayer via the recorded actor.
function actorFor(state) {
  if (state.turn != null) return state.turn;
  if (state.phase === 'roundStartDiscard' || state.phase === 'roundStartPlace') {
    for (let i = 0; i < state.playerCount; i++) {
      const sel = state.pending.selections[i];
      if (state.phase === 'roundStartDiscard' ? !sel : sel == null) return i;
    }
  }
  throw new Error(`no actor in phase ${state.phase}`);
}

const CONFIGS = [
  // playerCount 2 (4 shards each)
  { players: 2, shardIds: ['autonomy', 'ruin', 'honor', 'preservation'] },
  { players: 2, shardIds: ['devotion', 'dominion', 'odium', 'cultivation'] },
  { players: 2, shardIds: ['cultivation', 'honor', 'odium', 'ruin'] },
  { players: 2, shardIds: ['autonomy', 'devotion', 'dominion', 'preservation'] },
  // playerCount 3 (4 shards each)
  { players: 3, shardIds: ['autonomy', 'cultivation', 'honor', 'ruin'] },
  { players: 3, shardIds: ['devotion', 'dominion', 'odium', 'preservation'] },
  { players: 3, shardIds: ['autonomy', 'odium', 'preservation', 'ruin'] },
  { players: 3, shardIds: ['cultivation', 'devotion', 'dominion', 'honor'] },
  // playerCount 4 (5 shards each)
  { players: 4, shardIds: ['autonomy', 'devotion', 'dominion', 'odium', 'ruin'] },
  { players: 4, shardIds: ['cultivation', 'devotion', 'honor', 'preservation', 'odium'] },
  { players: 4, shardIds: ['autonomy', 'cultivation', 'dominion', 'honor', 'preservation'] },
  { players: 4, shardIds: ['autonomy', 'cultivation', 'devotion', 'odium', 'ruin'] },
];
const SEEDS_PER_CONFIG = 3;

let total = 0;
CONFIGS.forEach((cfg, ci) => {
  for (let k = 0; k < SEEDS_PER_CONFIG; k++) {
    const seed = (ci + 1) * 10007 + k * 977 + 42;
    const driverSeed = (ci + 1) * 7919 + k * 131 + 7;
    const playerIds = [1, 2, 3, 4].slice(0, cfg.players);
    const playerNames = playerIds.map((i) => `P${i}`);

    let state = createGame({ playerIds, playerNames, shardIds: cfg.shardIds, seed });
    const trace = {
      seed,
      driverSeed,
      playerCount: cfg.players,
      playerIds,
      playerNames,
      shardIds: cfg.shardIds,
      initial: state,
      steps: [],
    };

    const rand = lcg(driverSeed);
    let guard = 0;
    while (state.phase !== 'gameOver') {
      const actor = actorFor(state);
      const actions = enumerateActions(state, actor);
      if (actions.length === 0) throw new Error(`no legal actions for actor ${actor} in ${state.phase}`);
      const idx = Math.floor(rand() * actions.length);
      const action = actions[idx];
      state = applyAction(state, actor, action);
      const step = { actor, idx, nActions: actions.length, action, state };
      // The first trace of each config also snapshots every per-player
      // redacted view (plus the spectator view) so viewFor parity is
      // checked, not just core-state parity. Sampled every 4th step (and at
      // game over) to keep trace files small; across 12 traces that still
      // covers every phase. JSON.parse(JSON.stringify(...)) normalizes the
      // views the same way the wire format would.
      if (k === 0 && (guard % 4 === 0 || state.phase === 'gameOver')) {
        step.views = JSON.parse(JSON.stringify({
          players: playerIds.map((_, i) => viewFor(state, i)),
          spectator: viewFor(state, null),
        }));
      }
      trace.steps.push(step);
      if (++guard > 3000) throw new Error('game did not terminate');
    }

    const name = `trace_p${cfg.players}_c${String(ci).padStart(2, '0')}_s${k}.json.gz`;
    writeFileSync(join(OUT_DIR, name), gzipSync(JSON.stringify(trace), { level: 9 }));
    total += 1;
    console.log(`${name}: ${trace.steps.length} steps, trumps used: ${state.usedTrumps.join(',')}`);
  }
});
console.log(`wrote ${total} traces to ${OUT_DIR}`);
