/**
 * Data hooks for the dashboard view.
 *
 * Deliberately not folded into `core/queries.ts`: these are dashboard-only
 * shapes (a batched monthly series, lazy id->name resolution for the picker)
 * that no other view needs, and the shared file is explicitly reserved for
 * lookups more than one view depends on.
 */

import { useQuery } from "@tanstack/react-query";
import { useMemo } from "react";
import { apiGet } from "../../core/client";
import type { components } from "../../core/api-types";
import type { DashboardState } from "../../core/viewstate";

export type MartPage = components["schemas"]["MartPage"];
export type AreaRow = components["schemas"]["Area"];
export type ProjectRow = components["schemas"]["Project"];

/** Cap requested rows at the API's own max page size (config.py `max_page_limit`). */
const MART_LIMIT = 1000;

/**
 * The batched monthly series for every currently-selected entity, one
 * request for the whole comparison — the API accepts a comma-separated id
 * list precisely so the multi-select UX doesn't fan out into N requests.
 */
export function useDashboardMart(state: DashboardState) {
  const { entityType, entityIds, usage, monthFrom, monthTo, minSample, includeFuture } = state;
  const idsCsv = entityIds.length ? entityIds.join(",") : undefined;
  const path = entityType === "area" ? "/marts/area-monthly" : "/marts/project-monthly";
  const idsKey = entityType === "area" ? "area_ids" : "project_ids";

  return useQuery({
    queryKey: [
      "dashboard-mart",
      entityType,
      entityIds,
      usage,
      monthFrom,
      monthTo,
      minSample,
      includeFuture,
    ],
    queryFn: () =>
      apiGet<MartPage>(path, {
        [idsKey]: idsCsv,
        usage: usage ?? undefined,
        month_from: monthFrom ?? undefined,
        month_to: monthTo ?? undefined,
        min_sample: minSample ?? undefined,
        include_future: includeFuture,
        limit: MART_LIMIT,
      }),
    enabled: entityIds.length > 0,
  });
}

/**
 * Resolves names for ids the mart response didn't cover — e.g. an id with
 * zero qualifying rows in the current filter window (never returned by the
 * mart, so it never carries a `name_en`), or a freshly restored URL that
 * names an entity before any mart data has loaded. Only fetches ids actually
 * missing from `known`, and only while there are any.
 */
export function useEntityNameFallback(
  entityType: DashboardState["entityType"],
  ids: number[],
  known: Map<number, string>,
) {
  const missing = useMemo(
    () => ids.filter((id) => !known.has(id)).sort((a, b) => a - b),
    [ids, known],
  );

  return useQuery({
    queryKey: ["dashboard-entity-names", entityType, missing],
    queryFn: async () => {
      const path = entityType === "area" ? "/dimensions/areas" : "/dimensions/projects";
      const rows = await Promise.all(
        missing.map((id) =>
          apiGet<AreaRow | ProjectRow>(`${path}/${id}`).catch(() => null),
        ),
      );
      const out = new Map<number, string>();
      rows.forEach((row, i) => {
        if (row) out.set(missing[i], row.name_en);
      });
      return out;
    },
    enabled: missing.length > 0,
    staleTime: 30 * 60 * 1000,
  });
}

/**
 * Search-as-you-type lookup behind the entity picker. Enabled only once the
 * caller says so (the picker gates this on focus/typing) so an idle,
 * nothing-selected dashboard makes zero network requests.
 */
export function useEntitySearch(
  entityType: DashboardState["entityType"],
  q: string,
  enabled: boolean,
) {
  const path = entityType === "area" ? "/dimensions/areas" : "/dimensions/projects";
  return useQuery({
    queryKey: ["dashboard-entity-search", entityType, q],
    queryFn: () => apiGet<{ items: (AreaRow | ProjectRow)[] }>(path, { q: q || undefined, limit: 10 }),
    enabled,
    staleTime: 30 * 1000,
  });
}
