/**
 * Data hooks for the "entity" kind filter pickers (area/project/building/developer).
 *
 * `core/queries.ts` only exports what more than one view needs — it has a
 * single-area lookup (`useArea`) because the map and dashboard both need it,
 * but not project/building/developer. Those are a listing-only concern, so
 * they live here instead of growing the shared file for a single consumer.
 */

import { useQuery } from "@tanstack/react-query";
import { apiGet } from "../../core/client";
import type { components } from "../../core/api-types";
import type { Page } from "../../core/queries";

type Schemas = components["schemas"];
export type DeveloperRow = Schemas["Developer"];
export type ProjectRow = Schemas["Project"];
export type BuildingRow = Schemas["Building"];

/** Long-lived: dimension tables change on the daily pipeline at most (matches queries.ts). */
const DIMENSION_STALE_MS = 30 * 60 * 1000;

export function useDevelopers(q?: string, limit = 20) {
  return useQuery({
    queryKey: ["developers", q ?? "", limit],
    queryFn: () =>
      apiGet<Page<DeveloperRow>>("/dimensions/developers", { q: q || undefined, limit }),
    staleTime: DIMENSION_STALE_MS,
  });
}

export function useProjectById(projectId: number | undefined) {
  return useQuery({
    queryKey: ["project", projectId],
    queryFn: () => apiGet<ProjectRow>(`/dimensions/projects/${projectId}`),
    enabled: projectId !== undefined,
    staleTime: DIMENSION_STALE_MS,
  });
}

export function useBuildingById(buildingId: number | undefined) {
  return useQuery({
    queryKey: ["building", buildingId],
    queryFn: () => apiGet<BuildingRow>(`/dimensions/buildings/${buildingId}`),
    enabled: buildingId !== undefined,
    staleTime: DIMENSION_STALE_MS,
  });
}

// There is no `/dimensions/developers/{id}` endpoint (checked against
// api-types.ts), so a developer picked before this session — e.g. arriving
// via a shared URL — cannot be resolved to a name at all. DeveloperFilterPicker
// keeps the label from the moment it was picked in this session and falls
// back to showing the bare id otherwise, rather than guessing a name.
