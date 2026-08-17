/**
 * The dashboard view: pick entities, compare a metric over time.
 *
 * Split into DashboardView (always mounted) and DashboardBody (mounted only
 * once an entity is selected) so that the expensive hooks — the mart series
 * fetch and the metrics-meta fetch — simply don't exist until there's
 * something to fetch. An idle, nothing-picked dashboard fires zero requests
 * beyond the entity picker's own (itself gated on focus, see EntityPicker).
 */

import { useMemo, useState } from "react";
import { ApiError } from "../../core/client";
import { useMetricsMeta } from "../../core/queries";
import type { Action } from "../../core/reducer";
import { useStore } from "../../core/store";
import type { DashboardState } from "../../core/viewstate";
import { Caveats, CollapseToggle, EmptyState, ErrorNote, Panel, Spinner, useCollapse } from "../../ui/components";
import { Controls } from "./Controls";
import "./dashboard.css";
import { EntityPicker } from "./EntityPicker";
import { SummaryStrip } from "./SummaryStrip";
import { TimeSeriesChart } from "./TimeSeriesChart";
import { buildDashboardSeries } from "./transforms";
import { useDashboardMart, useEntityNameFallback } from "./useDashboardMart";

function EntityTypeToggle({
  entityType,
  dispatch,
}: {
  entityType: DashboardState["entityType"];
  dispatch: (action: Action) => void;
}) {
  function set(next: DashboardState["entityType"]) {
    if (next === entityType) return;
    // Area ids and project ids are different namespaces — carrying the old
    // selection over would silently compare the wrong entities (or 404).
    dispatch({ type: "dashboard/set", patch: { entityType: next, entityIds: [] } });
  }
  return (
    <div className="row" style={{ gap: 4 }}>
      <button
        type="button"
        className={`btn sm${entityType === "area" ? " primary" : ""}`}
        onClick={() => set("area")}
      >
        Areas
      </button>
      <button
        type="button"
        className={`btn sm${entityType === "project" ? " primary" : ""}`}
        onClick={() => set("project")}
      >
        Projects
      </button>
    </div>
  );
}

function DashboardBody({
  dashboard,
  dispatch,
  names,
}: {
  dashboard: DashboardState;
  dispatch: (action: Action) => void;
  names: Map<number, string>;
}) {
  const mart = useDashboardMart(dashboard);
  const metricsMeta = useMetricsMeta();
  const [filtersOpen, toggleFiltersOpen] = useCollapse();

  // Fold in names the mart response already carries (every MartRow has one)
  // before asking the fallback hook to resolve anything — otherwise an id
  // picked from a URL restore, rather than through the picker, would fetch
  // its name a second time even though the mart call just supplied it.
  const knownNames = useMemo(() => {
    const m = new Map(names);
    for (const row of mart.data?.items ?? []) {
      if (!m.has(row.entity_id)) m.set(row.entity_id, row.name_en);
    }
    return m;
  }, [names, mart.data]);

  const fallback = useEntityNameFallback(dashboard.entityType, dashboard.entityIds, knownNames);
  const allNames = useMemo(() => {
    const m = new Map(knownNames);
    for (const [id, name] of fallback.data ?? []) m.set(id, name);
    return m;
  }, [knownNames, fallback.data]);

  const series = useMemo(
    () =>
      buildDashboardSeries(
        mart.data?.items ?? [],
        dashboard.entityIds,
        allNames,
        dashboard.metric,
        dashboard.transform,
      ),
    [mart.data, dashboard.entityIds, allNames, dashboard.metric, dashboard.transform],
  );

  const meta = metricsMeta.data as { methodology?: string; caveats?: string[] } | undefined;

  // An old, DLD-superseded area code among the picked ids 422s the whole
  // batched request. The server names exactly which id and which current
  // areas replace it (CLAUDE.md, AREA_CODE_MIGRATION_ANALYSIS.md); swap that
  // one id for the chosen candidate rather than making the user start over.
  function onPickArea(candidateId: number) {
    const oldId = Number((mart.error as ApiError | undefined)?.body?.old_area_id);
    const nextIds = dashboard.entityIds.map((id) => (id === oldId ? candidateId : id));
    dispatch({ type: "dashboard/set", patch: { entityIds: nextIds } });
  }

  return (
    <>
      <Panel
        title="Filters"
        actions={<CollapseToggle open={filtersOpen} onClick={toggleFiltersOpen} label="filters" />}
      >
        {filtersOpen ? <Controls state={dashboard} dispatch={dispatch} /> : null}
      </Panel>

      <Panel title="Trend" actions={mart.isFetching ? <Spinner /> : null}>
        <div className="stack">
          {mart.error ? (
            <ErrorNote error={mart.error} onPickArea={onPickArea} />
          ) : (
            <TimeSeriesChart series={series} metric={dashboard.metric} transform={dashboard.transform} />
          )}
          {mart.data?.missing_ids?.length ? (
            <span className="muted">
              No data at all for: {mart.data.missing_ids.map((id) => allNames.get(id) ?? `#${id}`).join(", ")}
            </span>
          ) : null}
          <Caveats caveats={meta?.caveats} methodology={meta?.methodology} />
        </div>
      </Panel>

      <Panel title="Summary">
        <SummaryStrip series={series} metric={dashboard.metric} />
      </Panel>
    </>
  );
}

export function DashboardView() {
  const dashboard = useStore((s) => s.state.dashboard);
  const dispatch = useStore((s) => s.dispatch);
  const [manualNames, setManualNames] = useState<Map<number, string>>(new Map());

  function addEntity(id: number, name: string) {
    setManualNames((prev) => new Map(prev).set(id, name));
    if (!dashboard.entityIds.includes(id)) {
      dispatch({ type: "dashboard/set", patch: { entityIds: [...dashboard.entityIds, id] } });
    }
  }

  function removeEntity(id: number) {
    dispatch({
      type: "dashboard/set",
      patch: { entityIds: dashboard.entityIds.filter((x) => x !== id) },
    });
  }

  return (
    <div className="stack">
      <Panel title="Compare" actions={<EntityTypeToggle entityType={dashboard.entityType} dispatch={dispatch} />}>
        <EntityPicker
          entityType={dashboard.entityType}
          entityIds={dashboard.entityIds}
          names={manualNames}
          onAdd={addEntity}
          onRemove={removeEntity}
        />
      </Panel>

      {dashboard.entityIds.length === 0 ? (
        <Panel>
          <EmptyState
            title="Pick an area or project to get started"
            hint="Search above and select one or more to compare — this view is built for comparing several side by side."
          />
        </Panel>
      ) : (
        <DashboardBody dashboard={dashboard} dispatch={dispatch} names={manualNames} />
      )}
    </div>
  );
}
