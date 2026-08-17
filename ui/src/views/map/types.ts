/**
 * Shapes returned by `/geo/areas`, `/geo/projects`, `/geo/buildings`.
 *
 * Hand-rolled rather than pulled from `api-types.ts`: those three operations
 * have `schema: {}` in the live openapi.json (the router returns a plain
 * dict, not a Pydantic response model), so the generator emits nothing
 * useful for them. These types are written against the actual response
 * bodies (verified live against the running stack), not guessed.
 *
 * Numeric metric fields are typed `number | string | null | undefined` on
 * purpose: the live `/geo/areas` response currently serialises them as JSON
 * numbers (the repository does `float(...)` before returning), unlike most
 * of the rest of this API which sends Postgres NUMERIC as a string. Typing
 * for both and coercing with `toNumberOrNull` (styling.ts) means this view
 * doesn't silently break the day that endpoint's serialisation changes to
 * match the others.
 */

export interface GeoPointGeometry {
  type: "Point";
  coordinates: [number, number];
}

export interface GeoPolygonGeometry {
  type: "Polygon" | "MultiPolygon";
  // Nested coordinate arrays, depth depends on Polygon vs MultiPolygon — not
  // worth modelling precisely, MapLibre consumes it as-is.
  coordinates: unknown;
}

export type GeoGeometry = GeoPointGeometry | GeoPolygonGeometry;

/**
 * `/geo/areas` feature properties.
 *
 * The metric fields are present as a group or absent as a group: an area
 * with no qualifying mart row for the current filters has none of
 * `metric_month`/`usage`/`sale_median_price_m2`/etc. at all, rather than
 * having them set to `null`. Confirmed live — see MapView's report.
 */
export interface AreaProperties {
  area_id: number;
  name_en: string;
  dld_area_code: string | null;
  zone_name: string | null;
  /** The load-bearing honesty flag: false means this feature's geometry is a
   * centroid fallback, not a real boundary. */
  has_boundary: boolean;
  metric_month?: string;
  usage?: string | null;
  sale_median_price_m2?: number | string | null;
  sale_cnt?: number | string | null;
  rent_median_annual_m2?: number | string | null;
  rent_cnt?: number | string | null;
  gross_yield_pct?: number | string | null;
}

export interface AreaFeature {
  type: "Feature";
  id?: number;
  geometry: GeoGeometry;
  properties: AreaProperties;
}

export interface AreaFeatureCollection {
  type: "FeatureCollection";
  features: AreaFeature[];
  applied?: Record<string, unknown>;
}

export interface ProjectProperties {
  project_id: number;
  name_en: string;
  area_id: number | null;
  status: string | null;
  geo_match_method: string | null;
  is_precise: boolean;
  geo_building_count: number | null;
  geo_spread_m: number | string | null;
}

export interface ProjectFeature {
  type: "Feature";
  id?: number;
  geometry: GeoPointGeometry;
  properties: ProjectProperties;
}

export interface ProjectFeatureCollection {
  type: "FeatureCollection";
  features: ProjectFeature[];
}

export interface BuildingProperties {
  building_id: number;
  name_en: string;
  area_id: number | null;
  project_id: number | null;
  floors: number | null;
  rooms: number | null;
  is_precise: boolean;
}

export interface BuildingFeature {
  type: "Feature";
  id?: number;
  geometry: GeoPointGeometry;
  properties: BuildingProperties;
}

export interface BuildingFeatureCollection {
  type: "FeatureCollection";
  features: BuildingFeature[];
}
