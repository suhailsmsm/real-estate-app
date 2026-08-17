/**
 * Toggles which of the entity's columns are visible. Persisted to
 * `listing.columns` — an empty array is the documented "use the entity's
 * default set" marker (viewstate.ts), so toggling always dispatches the
 * full resulting list rather than a delta, and "Reset to default" dispatches
 * `[]` rather than recomputing the defaults itself.
 */

import { defaultColumns, type EntityDescriptor } from "../../core/entities";

export function ColumnPicker({
  entity,
  columns,
  onChange,
}: {
  entity: EntityDescriptor;
  columns: string[];
  onChange: (columns: string[]) => void;
}) {
  const active = columns.length ? columns : defaultColumns(entity.id);

  function toggle(id: string) {
    const next = entity.columns.map((c) => c.id).filter((cid) => (cid === id ? !active.includes(cid) : active.includes(cid)));
    onChange(next);
  }

  return (
    <details className="col-picker">
      <summary className="btn sm subtle">Columns</summary>
      <div className="col-picker-menu stack">
        {entity.columns.map((c) => (
          <label key={c.id} className="row col-picker-item">
            <input type="checkbox" checked={active.includes(c.id)} onChange={() => toggle(c.id)} />
            {c.label}
          </label>
        ))}
        <button type="button" className="btn sm subtle" onClick={() => onChange([])}>
          Reset to default
        </button>
      </div>
    </details>
  );
}
