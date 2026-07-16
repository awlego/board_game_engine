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
// make it relevant. Colors are inherited so the form fits any lobby
// theme; pass labelStyle for accents.

export function defaultOptions(form) {
  return Object.fromEntries(
    Object.entries(form).map(([key, field]) => [key, field.default]));
}

export function CreateFormFields({ form, value, onChange, labelStyle }) {
  const set = (key, v) => onChange({ ...value, [key]: v });
  const inputStyle = {
    fontFamily: "inherit", fontSize: "inherit", maxWidth: 190,
  };
  return Object.entries(form).map(([key, field]) => {
    if (field.showIf && !field.showIf(value)) return null;
    return (
      <div key={key} style={{ fontSize: 12 }}>
        <div style={{ fontWeight: 700, marginBottom: 4, ...labelStyle }}>
          {field.label}:
        </div>
        {(field.type || "checkboxes") === "checkboxes" && field.choices.map((c) => (
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
            {field.choices.map((c) => (
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
