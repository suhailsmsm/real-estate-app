/**
 * The map view: an OSM-tiled MapLibre map with an area choropleth, honest
 * degradation to points where no boundary exists, and project/building pins
 * when Granularity is set to them.
 *
 * Granularity is purely user-driven (the Controls dropdown, or a shared
 * URL) — earlier this also auto-switched on zoom, but that fought a manual
 * choice: picking Buildings and then panning at all (a `zoomend` can fire
 * from float drift during a drag, not just a deliberate scroll-zoom) would
 * silently flip it back to Areas with no click from the user. Removed
 * rather than patched — a control whose value the app overwrites out from
 * under you isn't a control.
 *
 * The map instance is created exactly once (empty dependency array below)
 * and never rebuilt — every reaction to state after that is an imperative
 * `setData`/`setLayoutProperty`/`setPaintProperty` call, driven by
 * `styling.ts`'s pure functions. Recreating the map on every state change
 * would restart tile loading and reset the user's in-progress pan/zoom on
 * every filter tweak.
 *
 * NOTE ON THE PACKAGE'S API: the task brief for this view assumed
 * `import maplibregl from "maplibre-gl"` (a default export). The installed
 * maplibre-gl v6 ships ESM with no default export at all (verified against
 * node_modules/maplibre-gl/dist/maplibre-gl.d.ts — everything is a named
 * export, `Map` included) and this repo's tsconfig has neither
 * `esModuleInterop` nor `allowSyntheticDefaultImports`, so a default import
 * fails `tsc --noEmit` outright. Using named imports instead
 * (`import { Map } from "maplibre-gl"`, aliased to avoid shadowing the
 * built-in `Map`) is the only way this actually compiles against v6.
 */

import { useEffect, useRef, useState } from "react";
import { Map as MapLibreMap, setWorkerUrl } from "maplibre-gl";
import type { GeoJSONSource, MapLayerMouseEvent, StyleSpecification } from "maplibre-gl";
import "maplibre-gl/dist/maplibre-gl.css";

// See vite.config.ts's syncMapLibreWorkerAssets() for the full story: MapLibre
// needs its worker script (and that script's own sibling import,
// maplibre-gl-shared.mjs) served from a fixed, unhashed path with both files
// alongside each other — which is what Vite's public/ directory guarantees
// and a normal hashed import cannot. setWorkerUrl() is MapLibre's own
// supported way to point it there, and must run before any Map is
// constructed.
setWorkerUrl("/maplibre-gl-worker.mjs");
import { useStore } from "../../core/store";
import type { MapState } from "../../core/viewstate";
import { ErrorNote, Spinner } from "../../ui/components";
import { Controls } from "./Controls";
import { DetailPanel } from "./DetailPanel";
import { Legend } from "./Legend";
import { useAreasGeo, useBuildingsGeo, useProjectsGeo } from "./queries";
import {
  buildColorScale,
  buildHeightScale,
  buildRadiusScale,
  collectMetricValues,
  LAYER_ID,
  partitionAreaFeatures,
  SOURCE_ID,
  styleAreaFeatures,
  type AreaStylingContext,
} from "./styling";
import "./map.css";

/**
 * A free, no-key OSM raster source. Attribution is not optional — it's the
 * usage condition OSM's tile policy is offered under, not a nicety.
 */
const OSM_STYLE: StyleSpecification = {
  version: 8,
  sources: {
    osm: {
      type: "raster",
      tiles: ["https://tile.openstreetmap.org/{z}/{x}/{y}.png"],
      tileSize: 256,
      attribution: '© <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors',
    },
  },
  layers: [{ id: "osm", type: "raster", source: "osm" }],
};

const EMPTY_FC = { type: "FeatureCollection" as const, features: [] };

/** Treats a `moveend` echo of our own last dispatched viewport as a no-op,
 * so the state->map sync effect doesn't fight the map->state one and cause
 * jitter. Anything bigger than float noise is a real external change (URL
 * navigation, undo, the encoding toggle's pitch bump) that the map must
 * actually move to. */
function sameViewport(a: MapState["viewport"], b: MapState["viewport"]): boolean {
  return (
    Math.abs(a.lng - b.lng) < 1e-4 &&
    Math.abs(a.lat - b.lat) < 1e-4 &&
    Math.abs(a.zoom - b.zoom) < 0.01 &&
    Math.abs(a.pitch - b.pitch) < 0.5 &&
    Math.abs(a.bearing - b.bearing) < 0.5
  );
}

function addSourcesAndLayers(m: MapLibreMap) {
  m.addSource(SOURCE_ID.areaPolygons, { type: "geojson", data: EMPTY_FC });
  m.addSource(SOURCE_ID.areaDots, { type: "geojson", data: EMPTY_FC });
  m.addSource(SOURCE_ID.projects, { type: "geojson", data: EMPTY_FC });
  m.addSource(SOURCE_ID.buildings, { type: "geojson", data: EMPTY_FC });

  // Flat fill — the `color` encoding.
  m.addLayer({
    id: LAYER_ID.areaFill,
    type: "fill",
    source: SOURCE_ID.areaPolygons,
    paint: { "fill-color": ["get", "_color"], "fill-opacity": 0.75 },
  });
  m.addLayer({
    id: LAYER_ID.areaOutline,
    type: "line",
    source: SOURCE_ID.areaPolygons,
    paint: { "line-color": "#5a666c", "line-width": 0.75 },
  });
  // 3D extrusion — the `height` encoding. Hidden until selected; needs pitch
  // > 0 to read as height rather than a flat shape from directly overhead
  // (Controls.tsx bumps pitch when this is switched on).
  m.addLayer({
    id: LAYER_ID.areaExtrusion,
    type: "fill-extrusion",
    source: SOURCE_ID.areaPolygons,
    paint: {
      "fill-extrusion-color": ["get", "_color"],
      "fill-extrusion-height": ["get", "_height"],
      "fill-extrusion-opacity": 0.85,
    },
    layout: { visibility: "none" },
  });
  // Areas with no boundary: a circle, never a shape — the mark itself is the
  // honesty signal, on top of the colour/height it still carries.
  m.addLayer({
    id: LAYER_ID.areaDots,
    type: "circle",
    source: SOURCE_ID.areaDots,
    paint: {
      "circle-color": ["get", "_color"],
      "circle-radius": ["get", "_radius"],
      "circle-stroke-width": 1.5,
      "circle-stroke-color": "#ffffff",
    },
  });
  m.addLayer({
    id: LAYER_ID.projects,
    type: "circle",
    source: SOURCE_ID.projects,
    paint: {
      // Precise (Makani/Nominatim-validated) vs the coarse area-centroid
      // fallback every project in an area otherwise shares — same honesty
      // split as areas, one level down (docs/PROJECT_GEO_ENRICHMENT.md).
      "circle-color": ["case", ["get", "is_precise"], "#a85d14", "#8b979b"],
      "circle-radius": 5,
      "circle-stroke-width": 1,
      "circle-stroke-color": "#ffffff",
    },
    layout: { visibility: "none" },
  });
  m.addLayer({
    id: LAYER_ID.buildings,
    type: "circle",
    source: SOURCE_ID.buildings,
    paint: {
      "circle-color": "#a85d14",
      "circle-radius": 4,
      "circle-stroke-width": 1,
      "circle-stroke-color": "#ffffff",
    },
    layout: { visibility: "none" },
  });
}

export function MapView() {
  const map = useStore((s) => s.state.map);
  const dispatch = useStore((s) => s.dispatch);

  const containerRef = useRef<HTMLDivElement | null>(null);
  const mapRef = useRef<MapLibreMap | null>(null);
  const lastSyncedViewport = useRef<MapState["viewport"] | null>(null);
  const [ready, setReady] = useState(false);

  const areasQuery = useAreasGeo(map);
  const projectsQuery = useProjectsGeo(map.granularity === "projects");
  const buildingsQuery = useBuildingsGeo(map.granularity === "buildings", map);

  // ---- create the map once ----
  useEffect(() => {
    if (!containerRef.current) return;
    const initial = useStore.getState().state.map.viewport;
    lastSyncedViewport.current = initial;

    const m = new MapLibreMap({
      container: containerRef.current,
      style: OSM_STYLE,
      center: [initial.lng, initial.lat],
      zoom: initial.zoom,
      pitch: initial.pitch,
      bearing: initial.bearing,
      // MapLibre's WebGL context defaults to no MSAA, which leaves every
      // polygon edge — and especially the height encoding's fill-extrusion
      // blocks — with hard, jagged pixel steps ("looks like Minecraft").
      // This is the fix, not a paint-property tweak: it's a rasterizer
      // setting, not something any layer's paint spec controls. This
      // installed version (v6) nests it under canvasContextAttributes rather
      // than a top-level `antialias` (verified against maplibre-gl.d.ts).
      canvasContextAttributes: { antialias: true },
    });
    mapRef.current = m;

    m.on("load", () => {
      addSourcesAndLayers(m);

      const handleAreaClick = (e: MapLayerMouseEvent) => {
        const id = e.features?.[0]?.properties?.area_id;
        if (typeof id === "number") {
          useStore.getState().dispatch({ type: "map/set", patch: { selectedAreaId: id } });
        }
      };
      for (const id of [LAYER_ID.areaFill, LAYER_ID.areaExtrusion, LAYER_ID.areaDots]) {
        m.on("click", id, handleAreaClick);
        m.on("mouseenter", id, () => {
          m.getCanvas().style.cursor = "pointer";
        });
        m.on("mouseleave", id, () => {
          m.getCanvas().style.cursor = "";
        });
      }

      setReady(true);
    });

    // Camera moves fire continuously while dragging; only the settled
    // position is worth writing back (store.ts already excludes
    // viewport-only patches from undo history for the same reason).
    m.on("moveend", () => {
      const c = m.getCenter();
      const vp = { lng: c.lng, lat: c.lat, zoom: m.getZoom(), pitch: m.getPitch(), bearing: m.getBearing() };
      lastSyncedViewport.current = vp;
      useStore.getState().dispatch({ type: "map/set", patch: { viewport: vp } });
    });

    return () => {
      m.remove();
      mapRef.current = null;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps -- intentionally once; see file header
  }, []);

  // ---- external viewport changes (URL nav, undo, the height-pitch bump) ----
  useEffect(() => {
    const m = mapRef.current;
    if (!m) return;
    const vp = map.viewport;
    if (lastSyncedViewport.current && sameViewport(lastSyncedViewport.current, vp)) return;
    lastSyncedViewport.current = vp;
    m.jumpTo({ center: [vp.lng, vp.lat], zoom: vp.zoom, pitch: vp.pitch, bearing: vp.bearing });
  }, [map.viewport]);

  // ---- layer visibility: which granularity + which encoding ----
  useEffect(() => {
    const m = mapRef.current;
    if (!m || !ready) return;
    const showAreas = map.granularity === "areas";
    m.setLayoutProperty(LAYER_ID.areaDots, "visibility", showAreas ? "visible" : "none");
    m.setLayoutProperty(LAYER_ID.areaOutline, "visibility", showAreas ? "visible" : "none");
    m.setLayoutProperty(
      LAYER_ID.areaFill,
      "visibility",
      showAreas && map.encoding === "color" ? "visible" : "none",
    );
    m.setLayoutProperty(
      LAYER_ID.areaExtrusion,
      "visibility",
      showAreas && map.encoding === "height" ? "visible" : "none",
    );
    m.setLayoutProperty(
      LAYER_ID.projects,
      "visibility",
      map.granularity === "projects" ? "visible" : "none",
    );
    m.setLayoutProperty(
      LAYER_ID.buildings,
      "visibility",
      map.granularity === "buildings" ? "visible" : "none",
    );
  }, [ready, map.granularity, map.encoding]);

  // ---- area data: partition, scale, style, push to sources ----
  useEffect(() => {
    const m = mapRef.current;
    if (!m || !ready) return;
    const { polygons, dots } = partitionAreaFeatures(areasQuery.data);
    const values = collectMetricValues([...polygons, ...dots], map.semantics);
    const ctx: AreaStylingContext = {
      semantics: map.semantics,
      encoding: map.encoding,
      colorScale: buildColorScale(values),
      heightScale: buildHeightScale(values),
      radiusScale: buildRadiusScale(values),
    };
    const polySource = m.getSource(SOURCE_ID.areaPolygons) as GeoJSONSource | undefined;
    const dotSource = m.getSource(SOURCE_ID.areaDots) as GeoJSONSource | undefined;
    polySource?.setData({ type: "FeatureCollection", features: styleAreaFeatures(polygons, ctx) });
    dotSource?.setData({ type: "FeatureCollection", features: styleAreaFeatures(dots, ctx) });
  }, [ready, areasQuery.data, map.semantics, map.encoding]);

  // ---- project / building points ----
  useEffect(() => {
    const m = mapRef.current;
    if (!m || !ready || !projectsQuery.data) return;
    (m.getSource(SOURCE_ID.projects) as GeoJSONSource | undefined)?.setData(projectsQuery.data);
  }, [ready, projectsQuery.data]);

  useEffect(() => {
    const m = mapRef.current;
    if (!m || !ready || !buildingsQuery.data) return;
    (m.getSource(SOURCE_ID.buildings) as GeoJSONSource | undefined)?.setData(buildingsQuery.data);
  }, [ready, buildingsQuery.data]);

  return (
    <div className="map-root">
      <div ref={containerRef} className="map-canvas" />

      <div className="map-overlay map-overlay-top">
        <Controls />
        {areasQuery.isError ? <ErrorNote error={areasQuery.error} /> : null}
        {map.granularity === "buildings" && buildingsQuery.isError ? (
          <ErrorNote
            error={buildingsQuery.error}
            onPickArea={(id) => dispatch({ type: "map/set", patch: { selectedAreaId: id } })}
          />
        ) : null}
        {!ready || areasQuery.isLoading ? <Spinner label="Loading map data…" /> : null}
      </div>

      <div className="map-overlay map-overlay-bottom-left">
        <Legend data={areasQuery.data} semantics={map.semantics} />
      </div>

      {map.selectedAreaId !== null ? (
        <div className="map-overlay map-overlay-right">
          <DetailPanel
            data={areasQuery.data}
            areaId={map.selectedAreaId}
            semantics={map.semantics}
            onClose={() => dispatch({ type: "map/set", patch: { selectedAreaId: null } })}
          />
        </div>
      ) : null}
    </div>
  );
}
