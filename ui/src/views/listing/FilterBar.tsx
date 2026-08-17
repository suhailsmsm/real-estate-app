/**
 * Renders an entity's filters generically from `ENTITIES[entity].filters`,
 * dispatching to the shared `listing.filters` slice — no per-entity UI here,
 * so a new entity or a new filter needs no changes to this file, only to
 * entities.ts.
 */

import { useEffect, useRef, useState } from "react";
import type { EntityDescriptor, FilterDef } from "../../core/entities";
import type { ListingFilters } from "../../core/viewstate";
import { Field } from "../../ui/components";
import { useUsages } from "../../core/queries";
import { EntityFilterPicker } from "./EntityFilterPicker";

const DEBOUNCE_MS = 300;

/**
 * Free-text / numeric inputs keep their own draft value while typing and
 * only dispatch after a pause — dispatching per keystroke would refetch on
 * every character and spam undo history. The draft resyncs from `value`
 * whenever it changes for a reason other than this field's own typing
 * (Clear filters, entity switch, a copilot patch, browser back/forward).
 */
function DebouncedField({
  filter,
  value,
  onChange,
  inputType = "text",
}: {
  filter: FilterDef;
  value: string | undefined;
  onChange: (raw: string | undefined) => void;
  inputType?: "text" | "number";
}) {
  const [draft, setDraft] = useState(value ?? "");
  const timer = useRef<ReturnType<typeof setTimeout> | undefined>(undefined);

  useEffect(() => setDraft(value ?? ""), [value]);
  useEffect(() => () => clearTimeout(timer.current), []);

  function handleChange(raw: string) {
    setDraft(raw);
    clearTimeout(timer.current);
    timer.current = setTimeout(() => onChange(raw === "" ? undefined : raw), DEBOUNCE_MS);
  }

  return (
    <Field label={filter.label} help={filter.help}>
      <input type={inputType} value={draft} onChange={(e) => handleChange(e.target.value)} />
    </Field>
  );
}

function DateFilter({
  filter,
  value,
  onChange,
}: {
  filter: FilterDef;
  value: string | undefined;
  onChange: (v: string | undefined) => void;
}) {
  return (
    <Field label={filter.label} help={filter.help}>
      <input type="date" value={value ?? ""} onChange={(e) => onChange(e.target.value || undefined)} />
    </Field>
  );
}

/**
 * Tri-state, not a checkbox: "unset" and "false" are different queries
 * (e.g. `is_offplan` absent means no filtering at all, `false` means
 * resale-only), so the control must be able to express all three.
 */
function BoolFilter({
  filter,
  value,
  onChange,
}: {
  filter: FilterDef;
  value: boolean | undefined;
  onChange: (v: boolean | undefined) => void;
}) {
  const current = value === undefined ? "" : value ? "true" : "false";
  return (
    <Field label={filter.label} help={filter.help}>
      <select
        value={current}
        onChange={(e) => onChange(e.target.value === "" ? undefined : e.target.value === "true")}
      >
        <option value="">Any</option>
        <option value="true">Yes</option>
        <option value="false">No</option>
      </select>
    </Field>
  );
}

function EnumFilter({
  filter,
  value,
  onChange,
}: {
  filter: FilterDef;
  value: string | undefined;
  onChange: (v: string | undefined) => void;
}) {
  // Static options win when given; "usage" is the one enum whose vocabulary
  // can't be hardcoded (entities.ts's usageFilter comment) — load it live.
  const usages = useUsages();
  const options = filter.options ?? (filter.id === "usage" ? usages.data?.map((u) => u.usage) : undefined) ?? [];

  return (
    <Field label={filter.label} help={filter.help}>
      <select value={value ?? ""} onChange={(e) => onChange(e.target.value || undefined)}>
        <option value="">Any</option>
        {options.map((o) => (
          <option key={o} value={o}>
            {o}
          </option>
        ))}
      </select>
    </Field>
  );
}

export function FilterBar({
  entity,
  filters,
  onSetFilter,
  onClear,
}: {
  entity: EntityDescriptor;
  filters: ListingFilters;
  onSetFilter: (key: keyof ListingFilters, value: unknown) => void;
  onClear: () => void;
}) {
  return (
    <div className="toolbar">
      {entity.filters.map((f) => {
        const key = f.id as keyof ListingFilters;
        const value = filters[key];

        switch (f.kind) {
          case "text":
            return (
              <DebouncedField
                key={f.id}
                filter={f}
                value={value as string | undefined}
                onChange={(v) => onSetFilter(key, v)}
              />
            );
          case "number":
            return (
              <DebouncedField
                key={f.id}
                filter={f}
                inputType="number"
                value={value === undefined ? undefined : String(value)}
                onChange={(v) => onSetFilter(key, v === undefined ? undefined : Number(v))}
              />
            );
          case "date":
            return (
              <DateFilter key={f.id} filter={f} value={value as string | undefined} onChange={(v) => onSetFilter(key, v)} />
            );
          case "bool":
            return (
              <BoolFilter key={f.id} filter={f} value={value as boolean | undefined} onChange={(v) => onSetFilter(key, v)} />
            );
          case "enum":
            return (
              <EnumFilter key={f.id} filter={f} value={value as string | undefined} onChange={(v) => onSetFilter(key, v)} />
            );
          case "entity":
            return (
              <EntityFilterPicker
                key={f.id}
                filter={f}
                value={value as number | undefined}
                onChange={(id) => onSetFilter(key, id)}
              />
            );
          default:
            return null;
        }
      })}
      <button type="button" className="btn subtle sm" onClick={onClear}>
        Clear filters
      </button>
    </div>
  );
}
