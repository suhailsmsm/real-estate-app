/**
 * Shared React Query hooks.
 *
 * Only the lookups more than one view needs live here — area/project/building
 * pickers, the usage vocabulary, coverage. View-specific queries belong with
 * their view. The point is that three views don't end up with three subtly
 * different ideas of how to search for an area.
 */

import { useQuery } from "@tanstack/react-query";
import { apiGet } from "./client";
import type { components } from "./api-types";

type Schemas = components["schemas"];

export interface Page<T> {
  items: T[];
  total: number | null;
  limit: number;
  offset: number;
  has_more: boolean;
}

export type AreaRow = Schemas["Area"];
export type ProjectRow = Schemas["Project"];
export type BuildingRow = Schemas["Building"];

/** Long-lived: the dimension tables change on the daily pipeline at most. */
const DIMENSION_STALE_MS = 30 * 60 * 1000;

export function useAreas(q?: string, limit = 50) {
  return useQuery({
    queryKey: ["areas", q ?? "", limit],
    queryFn: () => apiGet<Page<AreaRow>>("/dimensions/areas", { q: q || undefined, limit }),
    staleTime: DIMENSION_STALE_MS,
  });
}

export function useProjects(q?: string, areaId?: number, limit = 50) {
  return useQuery({
    queryKey: ["projects", q ?? "", areaId ?? null, limit],
    queryFn: () =>
      apiGet<Page<ProjectRow>>("/dimensions/projects", {
        q: q || undefined,
        area_id: areaId,
        limit,
      }),
    staleTime: DIMENSION_STALE_MS,
  });
}

export function useBuildings(q?: string, areaId?: number, limit = 50) {
  return useQuery({
    queryKey: ["buildings", q ?? "", areaId ?? null, limit],
    queryFn: () =>
      apiGet<Page<BuildingRow>>("/dimensions/buildings", {
        q: q || undefined,
        area_id: areaId,
        limit,
      }),
    staleTime: DIMENSION_STALE_MS,
  });
}

export interface UsageRow {
  usage: string;
  property_type_count: number;
}

/**
 * The real usage vocabulary.
 *
 * Always fetched, never hardcoded: these strings are raw DLD values, so a
 * plausible-looking guess ("Office") silently matches nothing and returns an
 * empty table that looks like missing data rather than a bad filter.
 */
export function useUsages() {
  return useQuery({
    queryKey: ["usages"],
    // Returns a BARE ARRAY, not a paged {items: [...]} envelope — unlike every
    // other dimension endpoint. Assuming the envelope here silently yields []
    // on every response, which shows up as a permanently empty usage filter
    // rather than as an error, so it is worth stating rather than inferring.
    queryFn: () => apiGet<UsageRow[]>("/dimensions/usages"),
    staleTime: DIMENSION_STALE_MS,
  });
}

/** Where the data starts and stops — needed to bound date pickers honestly. */
export function useCoverage() {
  return useQuery({
    queryKey: ["coverage"],
    queryFn: () => apiGet<Record<string, unknown>>("/meta/coverage"),
    staleTime: DIMENSION_STALE_MS,
  });
}

/** Metric definitions and their standing caveats, straight from the API. */
export function useMetricsMeta() {
  return useQuery({
    queryKey: ["metrics-meta"],
    queryFn: () => apiGet<Record<string, unknown>>("/meta/metrics"),
    staleTime: DIMENSION_STALE_MS,
  });
}

/** Resolve one area id to its row, for showing a picked filter's name. */
export function useArea(areaId: number | undefined) {
  return useQuery({
    queryKey: ["area", areaId],
    queryFn: () => apiGet<AreaRow>(`/dimensions/areas/${areaId}`),
    enabled: areaId !== undefined,
    staleTime: DIMENSION_STALE_MS,
  });
}
