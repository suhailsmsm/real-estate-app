/**
 * Search-and-multi-select for the entities being compared.
 *
 * This is a comparison tool first (docs: several entities is the normal
 * case), so picked entities render as removable chips with real names, never
 * bare ids — an id on its own tells a user nothing about which line is
 * theirs on the chart below.
 */

import { useRef, useState } from "react";
import { colorForEntity } from "./transforms";
import { useEntitySearch } from "./useDashboardMart";
import type { AreaRow, ProjectRow } from "./useDashboardMart";
import type { DashboardState } from "../../core/viewstate";

export function EntityPicker({
  entityType,
  entityIds,
  names,
  onAdd,
  onRemove,
}: {
  entityType: DashboardState["entityType"];
  entityIds: number[];
  names: Map<number, string>;
  onAdd: (id: number, name: string) => void;
  onRemove: (id: number) => void;
}) {
  const [query, setQuery] = useState("");
  const [open, setOpen] = useState(false);
  const boxRef = useRef<HTMLDivElement>(null);

  // Search fires only once the box has focus — an idle picker with nothing
  // typed makes no request, which matters most on first load before the user
  // has picked anything at all.
  const { data, isFetching } = useEntitySearch(entityType, query, open);
  const results = ((data?.items ?? []) as (AreaRow | ProjectRow)[]).filter(
    (r) => !entityIds.includes(r.id),
  );

  function pick(row: AreaRow | ProjectRow) {
    onAdd(row.id, row.name_en);
    setQuery("");
  }

  return (
    <div className="stack" style={{ gap: 8 }}>
      <div className="entity-picker" ref={boxRef}>
        <input
          type="text"
          placeholder={entityType === "area" ? "Search areas…" : "Search projects…"}
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          onFocus={() => setOpen(true)}
          onBlur={() => {
            // Delay so a click on a dropdown option registers before the
            // blur closes it — a bare onBlur would fire first and the click
            // would land on nothing.
            window.setTimeout(() => setOpen(false), 150);
          }}
          aria-label={entityType === "area" ? "Search areas" : "Search projects"}
        />
        {open && (query.length > 0 || results.length > 0) ? (
          <div className="entity-picker-menu" role="listbox">
            {isFetching ? <div className="entity-picker-row muted">Searching…</div> : null}
            {!isFetching && results.length === 0 ? (
              <div className="entity-picker-row muted">No matches</div>
            ) : null}
            {results.map((r) => (
              <button
                key={r.id}
                type="button"
                className="entity-picker-row"
                role="option"
                aria-selected={false}
                onMouseDown={(e) => e.preventDefault()} // survive the input's onBlur
                onClick={() => pick(r)}
              >
                {r.name_en}
              </button>
            ))}
          </div>
        ) : null}
      </div>

      {entityIds.length > 0 ? (
        <div className="row" style={{ flexWrap: "wrap" }}>
          {entityIds.map((id) => (
            <span key={id} className="chip">
              <span className="chip-swatch" style={{ background: colorForEntity(id) }} />
              {names.get(id) ?? `#${id}`}
              <button
                type="button"
                aria-label={`Remove ${names.get(id) ?? `#${id}`}`}
                onClick={() => onRemove(id)}
              >
                ×
              </button>
            </span>
          ))}
        </div>
      ) : null}
    </div>
  );
}
