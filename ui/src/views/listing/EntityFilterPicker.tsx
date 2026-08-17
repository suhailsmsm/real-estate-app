/**
 * Searchable picker for the "entity" kind filters (area/project/building/developer).
 *
 * Shows the resolved name once a value is picked, never a bare id — a raw
 * `area_id: 292` in a filter chip is meaningless to a user. Split into one
 * thin wrapper per entity kind (rather than one component switching on kind
 * internally) so each wrapper always calls the same hooks in the same order;
 * switching which `useQuery` a component calls between renders is exactly
 * the kind of conditional-hook bug that is easy to introduce with a single
 * generic component and hard to spot in review.
 */

import { useState } from "react";
import { useAreas, useArea, useProjects, useBuildings } from "../../core/queries";
import type { FilterDef } from "../../core/entities";
import { Field } from "../../ui/components";
import { useDebouncedValue } from "./useDebouncedValue";
import { useDevelopers, useProjectById, useBuildingById } from "./entityLookups";

interface ComboOption {
  id: number;
  label: string;
  sub?: string;
}

interface PickerProps {
  value: number | undefined;
  onChange: (id: number | undefined) => void;
}

function EntityCombo({
  fieldLabel,
  help,
  placeholder,
  value,
  label,
  loadingLabel,
  query,
  onQueryChange,
  results,
  loadingResults,
  onSelect,
  onClear,
}: {
  fieldLabel: string;
  // Field (ui/components.tsx) renders help as a hover/focus tooltip, plain text only.
  help?: string;
  placeholder: string;
  value: number | undefined;
  label: string | undefined;
  loadingLabel: boolean;
  query: string;
  onQueryChange: (q: string) => void;
  results: ComboOption[];
  loadingResults: boolean;
  onSelect: (id: number, label: string) => void;
  onClear: () => void;
}) {
  const [open, setOpen] = useState(false);

  return (
    <Field label={fieldLabel} help={help}>
      {value !== undefined ? (
        <div className="row entity-chip">
          <span className="badge" title={loadingLabel ? undefined : label}>
            {label ?? (loadingLabel ? "Loading…" : `#${value}`)}
          </span>
          <button
            type="button"
            className="btn subtle sm"
            onClick={onClear}
            aria-label={`Clear ${fieldLabel}`}
          >
            ×
          </button>
        </div>
      ) : (
        <div className="combo">
          <input
            value={query}
            placeholder={placeholder}
            onChange={(e) => {
              onQueryChange(e.target.value);
              setOpen(true);
            }}
            onFocus={() => setOpen(true)}
            // Delay so a click on a menu item (which blurs the input first) still lands.
            onBlur={() => setTimeout(() => setOpen(false), 150)}
          />
          {open && query ? (
            <div className="combo-menu">
              {loadingResults ? (
                <div className="combo-item muted">Searching…</div>
              ) : results.length ? (
                results.map((r) => (
                  <button
                    type="button"
                    key={r.id}
                    className="combo-item"
                    onMouseDown={(e) => e.preventDefault()}
                    onClick={() => {
                      onSelect(r.id, r.label);
                      setOpen(false);
                    }}
                  >
                    {r.label}
                    {r.sub ? <span className="faint"> · {r.sub}</span> : null}
                  </button>
                ))
              ) : (
                <div className="combo-item muted">No matches</div>
              )}
            </div>
          ) : null}
        </div>
      )}
    </Field>
  );
}

export function AreaFilterPicker({ value, onChange, fieldLabel, help }: PickerProps & { fieldLabel: string; help?: string }) {
  const [query, setQuery] = useState("");
  const debounced = useDebouncedValue(query, 300);
  const { data, isFetching } = useAreas(debounced || undefined, 20);
  const { data: selected, isFetching: loadingSelected } = useArea(value);

  return (
    <EntityCombo
      fieldLabel={fieldLabel}
      help={help}
      placeholder="Search areas…"
      value={value}
      label={selected?.name_en}
      loadingLabel={loadingSelected}
      query={query}
      onQueryChange={setQuery}
      results={(data?.items ?? []).map((a) => ({
        id: a.id,
        label: a.name_en,
        sub: a.dld_area_code ?? undefined,
      }))}
      loadingResults={isFetching}
      onSelect={(id) => {
        onChange(id);
        setQuery("");
      }}
      onClear={() => onChange(undefined)}
    />
  );
}

export function ProjectFilterPicker({ value, onChange, fieldLabel, help }: PickerProps & { fieldLabel: string; help?: string }) {
  const [query, setQuery] = useState("");
  const debounced = useDebouncedValue(query, 300);
  const { data, isFetching } = useProjects(debounced || undefined, undefined, 20);
  const { data: selected, isFetching: loadingSelected } = useProjectById(value);

  return (
    <EntityCombo
      fieldLabel={fieldLabel}
      help={help}
      placeholder="Search projects…"
      value={value}
      label={selected?.name_en}
      loadingLabel={loadingSelected}
      query={query}
      onQueryChange={setQuery}
      results={(data?.items ?? []).map((p) => ({ id: p.id, label: p.name_en }))}
      loadingResults={isFetching}
      onSelect={(id) => {
        onChange(id);
        setQuery("");
      }}
      onClear={() => onChange(undefined)}
    />
  );
}

export function BuildingFilterPicker({ value, onChange, fieldLabel, help }: PickerProps & { fieldLabel: string; help?: string }) {
  const [query, setQuery] = useState("");
  const debounced = useDebouncedValue(query, 300);
  const { data, isFetching } = useBuildings(debounced || undefined, undefined, 20);
  const { data: selected, isFetching: loadingSelected } = useBuildingById(value);

  return (
    <EntityCombo
      fieldLabel={fieldLabel}
      help={help}
      placeholder="Search buildings…"
      value={value}
      label={selected?.name_en}
      loadingLabel={loadingSelected}
      query={query}
      onQueryChange={setQuery}
      results={(data?.items ?? []).map((b) => ({ id: b.id, label: b.name_en }))}
      loadingResults={isFetching}
      onSelect={(id) => {
        onChange(id);
        setQuery("");
      }}
      onClear={() => onChange(undefined)}
    />
  );
}

export function DeveloperFilterPicker({ value, onChange, fieldLabel, help }: PickerProps & { fieldLabel: string; help?: string }) {
  const [query, setQuery] = useState("");
  // No GET /dimensions/developers/{id} exists, so unlike area/project/building
  // this label cannot be re-resolved on reload — only remembered from the
  // moment it was picked in this session (see entityLookups.ts).
  const [pickedLabel, setPickedLabel] = useState<string | undefined>(undefined);
  const debounced = useDebouncedValue(query, 300);
  const { data, isFetching } = useDevelopers(debounced || undefined, 20);

  return (
    <EntityCombo
      fieldLabel={fieldLabel}
      help={
        value !== undefined && !pickedLabel
          ? "No name lookup for a developer id from a shared link — re-pick to see the name."
          : help
      }
      placeholder="Search developers…"
      value={value}
      label={pickedLabel}
      loadingLabel={false}
      query={query}
      onQueryChange={setQuery}
      results={(data?.items ?? []).map((d) => ({ id: d.id, label: d.name_en }))}
      loadingResults={isFetching}
      onSelect={(id, label) => {
        onChange(id);
        setPickedLabel(label);
        setQuery("");
      }}
      onClear={() => {
        onChange(undefined);
        setPickedLabel(undefined);
      }}
    />
  );
}

export function EntityFilterPicker({
  filter,
  value,
  onChange,
}: {
  filter: FilterDef;
  value: number | undefined;
  onChange: (id: number | undefined) => void;
}) {
  // FilterDef guarantees `entity` is set whenever kind is "entity" (entities.ts).
  switch (filter.entity) {
    case "area":
      return <AreaFilterPicker value={value} onChange={onChange} fieldLabel={filter.label} help={filter.help} />;
    case "project":
      return <ProjectFilterPicker value={value} onChange={onChange} fieldLabel={filter.label} help={filter.help} />;
    case "building":
      return <BuildingFilterPicker value={value} onChange={onChange} fieldLabel={filter.label} help={filter.help} />;
    case "developer":
      return <DeveloperFilterPicker value={value} onChange={onChange} fieldLabel={filter.label} help={filter.help} />;
    default:
      return null;
  }
}
