/**
 * The listing view: a configurable data table over the five entities in
 * entities.ts. Deliberately generic — nothing here names a specific entity
 * or column; every control is driven by `ENTITIES[state.listing.entity]` so
 * a new entity added to entities.ts needs no changes here.
 *
 * All configuration lives in `state.listing` (viewstate.ts), never in local
 * `useState` — that's what makes the view's URL shareable and lets the
 * copilot drive it through the same reducer a click goes through.
 */

import { useCallback } from "react";
import { ENTITIES } from "../../core/entities";
import { useStore } from "../../core/store";
import { LISTING_ENTITIES, type ListingEntity, type ListingFilters } from "../../core/viewstate";
import { CollapseToggle, EmptyState, ErrorNote, Field, Note, Panel, Spinner, useCollapse } from "../../ui/components";
import { ColumnPicker } from "./ColumnPicker";
import { FilterBar } from "./FilterBar";
import { ListingTable } from "./ListingTable";
import "./listing.css";
import { Pagination } from "./Pagination";
import { useListingQuery } from "./useListingQuery";

export function ListingView() {
  const listing = useStore((s) => s.state.listing);
  const dispatch = useStore((s) => s.dispatch);
  const [filtersOpen, toggleFiltersOpen] = useCollapse();

  const setFilter = useCallback(
    (key: keyof ListingFilters, value: unknown) => dispatch({ type: "listing/setFilter", key, value }),
    [dispatch],
  );

  const entity = ENTITIES[listing.entity];
  const activeColumnIds = listing.columns.length
    ? listing.columns
    : entity.columns.filter((c) => c.default).map((c) => c.id);
  const visibleColumns = entity.columns.filter((c) => activeColumnIds.includes(c.id));

  const { data, isLoading, isFetching, error, missing } = useListingQuery(listing);

  const missingLabels = missing
    ? (entity.requiresOneOf ?? []).map((id) => entity.filters.find((f) => f.id === id)?.label ?? id)
    : [];

  return (
    <div className="stack listing-view">
      <Panel
        title="Listing"
        actions={
          <div className="row">
            <Field label="Entity">
              <select
                aria-label="Entity"
                value={listing.entity}
                onChange={(e) => dispatch({ type: "listing/setEntity", entity: e.target.value as ListingEntity })}
              >
                {LISTING_ENTITIES.map((id) => (
                  <option key={id} value={id}>
                    {ENTITIES[id].label}
                  </option>
                ))}
              </select>
            </Field>
            <ColumnPicker
              entity={entity}
              columns={listing.columns}
              onChange={(columns) => dispatch({ type: "listing/setColumns", columns })}
            />
            <CollapseToggle open={filtersOpen} onClick={toggleFiltersOpen} label="filters" />
          </div>
        }
      >
        {filtersOpen ? (
          <FilterBar
            entity={entity}
            filters={listing.filters}
            onSetFilter={setFilter}
            onClear={() => dispatch({ type: "listing/clearFilters" })}
          />
        ) : null}
      </Panel>

      <Panel flush>
        <div className="panel-body stack">
          {missing ? (
            <Note tone="warn">
              {entity.label} needs at least one of: {missingLabels.join(", ")}. Set one above to load results — an
              unfiltered scan of this table would time out, so the API refuses it outright.
            </Note>
          ) : error ? (
            <ErrorNote error={error} onPickArea={(areaId) => setFilter("area_id", areaId)} />
          ) : isLoading ? (
            <Spinner label={`Loading ${entity.label.toLowerCase()}…`} />
          ) : !data || data.items.length === 0 ? (
            <EmptyState title="No results" hint="Try widening the filters." />
          ) : (
            <>
              <ListingTable
                columns={visibleColumns}
                rows={data.items}
                sort={listing.sort}
                onSortChange={(sort) => dispatch({ type: "listing/setSort", sort })}
              />
              <Note tone="info">Sorting applies to the loaded page, not the full result set — page through to see more.</Note>
              <Pagination
                offset={listing.offset}
                limit={listing.limit}
                rowCount={data.items.length}
                total={data.total}
                hasTotal={entity.hasTotal}
                hasMore={data.has_more}
                onPageChange={(offset) => dispatch({ type: "listing/setPage", offset })}
                onLimitChange={(limit) => dispatch({ type: "listing/setLimit", limit })}
              />
              {isFetching ? <Spinner label="Refreshing…" /> : null}
            </>
          )}
        </div>
      </Panel>
    </div>
  );
}
