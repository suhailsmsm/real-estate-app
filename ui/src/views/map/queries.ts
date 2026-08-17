/**
 * Map-specific React Query hooks.
 *
 * `/geo/projects` and `/geo/buildings` carry no metrics at all (verified
 * live against the running stack — see MapView's report) and no filters
 * beyond an id scope, so unlike `/geo/areas` they don't vary with semantics,
 * month range, usage or min-sample. Query keys reflect exactly that: areas
 * refetch on every filter that changes what's drawn, projects/buildings only
 * on the scope that changes which points come back.
 */

import { useQuery } from "@tanstack/react-query";
import { apiGet } from "../../core/client";
import type { MapState } from "../../core/viewstate";
import { buildAreasParams, buildBuildingsParams } from "./styling";
import type { AreaFeatureCollection, BuildingFeatureCollection, ProjectFeatureCollection } from "./types";

/** The mart is refreshed by the daily pipeline at most; no point refetching
 * more often than that within a session. */
const GEO_STALE_MS = 5 * 60 * 1000;

export function useAreasGeo(map: MapState) {
  const params = buildAreasParams(map);
  return useQuery({
    queryKey: ["geo-areas", params],
    queryFn: () => apiGet<AreaFeatureCollection>("/geo/areas", params),
    staleTime: GEO_STALE_MS,
  });
}

export function useProjectsGeo(enabled: boolean) {
  return useQuery({
    queryKey: ["geo-projects"],
    queryFn: () => apiGet<ProjectFeatureCollection>("/geo/projects", {}),
    enabled,
    staleTime: GEO_STALE_MS,
  });
}

export function useBuildingsGeo(enabled: boolean, map: MapState) {
  const params = buildBuildingsParams(map);
  return useQuery({
    queryKey: ["geo-buildings", params],
    queryFn: () => apiGet<BuildingFeatureCollection>("/geo/buildings", params),
    enabled,
    staleTime: GEO_STALE_MS,
  });
}
