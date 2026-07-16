// Generic create-room options form, driven by a game's CREATE_FORM spec
// ({ key: field }). Rendered by BOTH the game-selector lobby (main.jsx)
// and a game client's own lobby so the two create dialogs can't drift.
//
// Field types:
//   checkboxes — { choices: [{id,label}], default: [ids] } → array value
//   select     — { choices: [{id,label}], default: id }    → id value
//   number     — { min, max, default }                     → int value
//                ("" while the user clears the input; servers treat a
//                non-integer as the default)
// A field may declare showIf(opts) to hide itself until other options
// make it relevant, and/or choicesFrom: "<key>" to append choices
// looked up in the dynamicChoices prop (fetched by the lobby, e.g.
// saved card sets); a choicesFrom field with fewer than 2 merged
// choices hides itself entirely. Colors are inherited so the form fits
// any lobby theme; pass labelStyle for accents.

import { WS_URL } from "../ws.js";

export function defaultOptions(form) {
  return Object.fromEntries(
    Object.entries(form).map(([key, field]) => [key, field.default]));
}

// One-shot fetch of a game's saved card sets (see server card_sets.py),
// mapped to select choices for a choicesFrom: "card_sets" field. Used
// by both lobbies' create dialogs. Resolves to [] on any failure — the
// create dialog then simply offers decks only.
export function fetchCardSets(game) {
  return new Promise((resolve) => {
    let ws;
    try { ws = new WebSocket(WS_URL); } catch { resolve([]); return; }
    const done = (sets) => { resolve(sets); ws.close(); };
    ws.onopen = () => ws.send(JSON.stringify({ type: "list_card_sets", game }));
    ws.onmessage = (evt) => {
      const msg = JSON.parse(evt.data);
      if (msg.type === "card_set_list") done(msg.sets || []);
    };
    ws.onerror = () => done([]);
  });
}

export function cardSetChoices(sets) {
  return sets.map((s) => ({
    id: s.id,
    label: `${s.name} (${(s.cards || []).length} cards)`,
  }));
}

export function CreateFormFields({ form, value, onChange, labelStyle, dynamicChoices }) {
  const set = (key, v) => onChange({ ...value, [key]: v });
  const inputStyle = {
    fontFamily: "inherit", fontSize: "inherit", maxWidth: 190,
  };
  return Object.entries(form).map(([key, field]) => {
    if (field.showIf && !field.showIf(value)) return null;
    let choices = field.choices || [];
    if (field.choicesFrom) {
      choices = [...choices, ...((dynamicChoices || {})[field.choicesFrom] || [])];
      if (choices.length < 2) return null;
    }
    return (
      <div key={key} style={{ fontSize: 12 }}>
        <div style={{ fontWeight: 700, marginBottom: 4, ...labelStyle }}>
          {field.label}:
        </div>
        {(field.type || "checkboxes") === "checkboxes" && choices.map((c) => (
          <label key={c.id} style={{ display: "block", cursor: "pointer", padding: "1px 0" }}>
            <input type="checkbox" checked={value[key].includes(c.id)}
              onChange={(e) => set(key, e.target.checked
                ? [...value[key], c.id]
                : value[key].filter((x) => x !== c.id))} />
            {" "}{c.label}
          </label>
        ))}
        {field.type === "select" && (
          <select style={inputStyle} value={value[key]}
            onChange={(e) => set(key, e.target.value)}>
            {choices.map((c) => (
              <option key={c.id} value={c.id}>{c.label}</option>
            ))}
          </select>
        )}
        {field.type === "number" && (
          <input type="number" style={{ ...inputStyle, width: 64 }}
            min={field.min} max={field.max} value={value[key]}
            onChange={(e) => {
              const n = parseInt(e.target.value, 10);
              set(key, Number.isNaN(n) ? "" : n);
            }} />
        )}
      </div>
    );
  });
}
