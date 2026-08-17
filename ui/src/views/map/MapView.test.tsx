/**
 * MapView smoke + integration tests.
 *
 * jsdom has no WebGL, so a real MapLibre map cannot be constructed here — the
 * `maplibre-gl` module is mocked with a minimal fake that records what
 * MapView does to it (sources added, layers added, data pushed, layout
 * properties toggled) without ever touching a canvas. That is deliberately
 * the boundary: styling.test.ts covers the actual colour/shape/data logic,
 * this file covers that MapView wires that logic to the map API correctly —
 * clicks dispatch, controls dispatch, data flows into the right source.
 */

import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { defaultViewState } from "../../core/viewstate";
import { useStore } from "../../core/store";

// ---- fake maplibre-gl ------------------------------------------------
//
// `vi.mock` factories are hoisted above every import, so the class they
// return has to be built inside `vi.hoisted` — anything referencing a
// normal top-level `class FakeMap {}` here would hit the temporal dead zone
// before the factory ever runs.

const { FakeMap, FakeGeoJSONSource } = vi.hoisted(() => {
  interface FakeLayer {
    id: string;
    type: string;
    layout?: Record<string, unknown>;
  }

  class FakeGeoJSONSource {
    data: unknown;
    setData(d: unknown) {
      this.data = d;
    }
  }

  /** Registered event handlers, exposed for tests to fire manually —
   * MapLibre itself is the thing under mock, its event loop doesn't run in
   * jsdom. */
  class FakeMap {
    static instances: FakeMap[] = [];
    handlers: Record<string, Array<(e?: unknown) => void>> = {};
    layerHandlers: Record<string, Record<string, Array<(e?: unknown) => void>>> = {};
    sources: Record<string, FakeGeoJSONSource> = {};
    layers: Record<string, FakeLayer> = {};
    removed = false;
    center = { lng: 0, lat: 0 };
    zoom = 9.4;
    pitch = 0;
    bearing = 0;
    jumpToCalls: unknown[] = [];

    constructor(_opts: unknown) {
      FakeMap.instances.push(this);
    }

    on(type: string, a: string | ((e?: unknown) => void), b?: (e?: unknown) => void) {
      if (typeof a === "string") {
        (this.layerHandlers[a] ??= {});
        (this.layerHandlers[a][type] ??= []).push(b!);
      } else {
        (this.handlers[type] ??= []).push(a);
      }
      return this;
    }

    fire(type: string) {
      for (const h of this.handlers[type] ?? []) h();
    }

    fireLayerClick(layerId: string, event: unknown) {
      for (const h of this.layerHandlers[layerId]?.click ?? []) h(event);
    }

    addSource(id: string, _spec: unknown) {
      this.sources[id] = new FakeGeoJSONSource();
    }
    getSource(id: string) {
      return this.sources[id];
    }
    addLayer(spec: FakeLayer) {
      this.layers[spec.id] = spec;
    }
    setLayoutProperty(id: string, key: string, value: unknown) {
      (this.layers[id].layout ??= {})[key] = value;
    }
    getCanvas() {
      return { style: {} } as HTMLCanvasElement;
    }
    getCenter() {
      return this.center;
    }
    getZoom() {
      return this.zoom;
    }
    getPitch() {
      return this.pitch;
    }
    getBearing() {
      return this.bearing;
    }
    jumpTo(opts: { center: [number, number]; zoom: number; pitch: number; bearing: number }) {
      this.jumpToCalls.push(opts);
      this.center = { lng: opts.center[0], lat: opts.center[1] };
      this.zoom = opts.zoom;
      this.pitch = opts.pitch;
      this.bearing = opts.bearing;
    }
    remove() {
      this.removed = true;
    }
  }

  return { FakeMap, FakeGeoJSONSource };
});

// setWorkerUrl is a required export now (MapView.tsx calls it at module load
// to fix a real worker-loading bug — see vite.config.ts's
// syncMapLibreWorkerAssets for the full story); it's a no-op stub here since
// this file mocks the whole Map lifecycle anyway.
vi.mock("maplibre-gl", () => ({ Map: FakeMap, setWorkerUrl: vi.fn() }));

import { MapView } from "./MapView";

// ---- fixtures, matching the real /geo/areas shape verified live ----

const AREAS_FC = {
  type: "FeatureCollection",
  features: [
    {
      type: "Feature",
      id: 299,
      geometry: { type: "MultiPolygon", coordinates: [] },
      properties: {
        area_id: 299,
        name_en: "AL FURJAN",
        dld_area_code: "C-4",
        zone_name: null,
        has_boundary: true,
        metric_month: "2026-08-01",
        usage: "Residential",
        sale_median_price_m2: 15633.8,
        sale_cnt: 20,
        rent_median_annual_m2: 1033.26,
        rent_cnt: 105,
        gross_yield_pct: 6.61,
      },
    },
    {
      type: "Feature",
      id: 322,
      geometry: { type: "Point", coordinates: [55.4095044, 24.7765552] },
      properties: {
        area_id: 322,
        name_en: "AL HATHMAH",
        dld_area_code: "A-512",
        zone_name: null,
        has_boundary: false,
      },
    },
  ],
};

function jsonResponse(body: unknown): Response {
  return new Response(JSON.stringify(body), { status: 200, headers: { "Content-Type": "application/json" } });
}

function renderMapView() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <MapView />
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  FakeMap.instances = [];
  useStore.setState({ state: defaultViewState(), past: [], future: [], lastPatchError: null });
  vi.stubGlobal(
    "fetch",
    vi.fn((url: string) => {
      if (url.includes("/geo/areas")) return Promise.resolve(jsonResponse(AREAS_FC));
      if (url.includes("/geo/projects")) return Promise.resolve(jsonResponse({ type: "FeatureCollection", features: [] }));
      if (url.includes("/geo/buildings")) return Promise.resolve(jsonResponse({ type: "FeatureCollection", features: [] }));
      // /dimensions/usages returns a bare array, unlike every other paged
      // dimension endpoint — core/queries.ts's useUsages() matches that
      // directly (verified live; see the coordinator's correction).
      if (url.includes("/dimensions/usages"))
        return Promise.resolve(jsonResponse([{ usage: "Residential", property_type_count: 10 }]));
      return Promise.resolve(jsonResponse({ items: [] }));
    }),
  );
});

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

describe("MapView lifecycle", () => {
  it("creates exactly one map on mount and removes it on unmount", () => {
    const { unmount } = renderMapView();
    expect(FakeMap.instances).toHaveLength(1);
    expect(FakeMap.instances[0].removed).toBe(false);
    unmount();
    expect(FakeMap.instances[0].removed).toBe(true);
  });

  it("adds the polygon, dot, project and building sources on load", () => {
    renderMapView();
    const m = FakeMap.instances[0];
    m.fire("load");
    expect(Object.keys(m.sources)).toEqual(
      expect.arrayContaining(["dxb-area-polygons", "dxb-area-dots", "dxb-projects", "dxb-buildings"]),
    );
  });
});

describe("MapView data flow", () => {
  it("splits fetched areas into the polygon source and the dot source honestly", async () => {
    renderMapView();
    const m = FakeMap.instances[0];
    m.fire("load");

    await waitFor(() => {
      const poly = m.getSource("dxb-area-polygons") as InstanceType<typeof FakeGeoJSONSource>;
      expect((poly.data as { features: unknown[] }).features).toHaveLength(1);
    });
    const dots = m.getSource("dxb-area-dots") as InstanceType<typeof FakeGeoJSONSource>;
    const polys = m.getSource("dxb-area-polygons") as InstanceType<typeof FakeGeoJSONSource>;
    expect((dots.data as { features: { properties: { area_id: number } }[] }).features[0].properties.area_id).toBe(
      322,
    );
    expect((polys.data as { features: { properties: { area_id: number } }[] }).features[0].properties.area_id).toBe(
      299,
    );
  });

  it("clicking an area feature sets selectedAreaId, which the store then exposes", async () => {
    renderMapView();
    const m = FakeMap.instances[0];
    m.fire("load");
    await waitFor(() => expect(Object.keys(m.sources).length).toBeGreaterThan(0));

    m.fireLayerClick("dxb-area-fill", { features: [{ properties: { area_id: 299 } }] });

    await waitFor(() => expect(useStore.getState().state.map.selectedAreaId).toBe(299));
    // The detail panel should now render the clicked area's name.
    expect(await screen.findByText("AL FURJAN")).toBeInTheDocument();
  });

  it("moveend writes the settled camera back into ViewState", () => {
    renderMapView();
    const m = FakeMap.instances[0];
    m.center = { lng: 55.3, lat: 25.2 };
    m.zoom = 11;
    m.pitch = 20;
    m.bearing = 5;
    m.fire("moveend");
    expect(useStore.getState().state.map.viewport).toEqual({
      lng: 55.3,
      lat: 25.2,
      zoom: 11,
      pitch: 20,
      bearing: 5,
    });
  });

  it("a manually-picked granularity survives panning/zooming — the reported bug", () => {
    // Granularity used to auto-switch on zoomend, which meant picking
    // "Buildings" and then so much as panning (zoomend can fire from float
    // drift during a drag, not only a deliberate scroll-zoom) silently flipped
    // it back to "Areas" with no click from the user. Regression guard for
    // that exact report: granularity is purely user/URL-driven now.
    renderMapView();
    useStore.getState().dispatch({ type: "map/set", patch: { granularity: "buildings" } });
    const m = FakeMap.instances[0];
    m.zoom = 9; // an "areas" zoom level, if auto-switching still existed
    m.fire("zoomend");
    m.fire("moveend");
    expect(useStore.getState().state.map.granularity).toBe("buildings");
  });
});

describe("MapView controls", () => {
  it("changing semantics dispatches map/set and re-renders the legend", async () => {
    renderMapView();
    const select = screen.getByLabelText("Semantics") as HTMLSelectElement;
    fireEvent.change(select, { target: { value: "yield" } });
    expect(useStore.getState().state.map.semantics).toBe("yield");
    expect(await screen.findByText(/Gross yield/)).toBeInTheDocument();
  });

  it("switching encoding to height bumps pitch when the camera is flat", () => {
    renderMapView();
    const buttons = screen.getAllByRole("button", { name: /Height/ });
    fireEvent.click(buttons[0]);
    const s = useStore.getState().state.map;
    expect(s.encoding).toBe("height");
    expect(s.viewport.pitch).toBeGreaterThan(0);
  });

  it("does not fight a pitch the user already set when toggling encoding", () => {
    useStore.setState((prev) => ({
      state: { ...prev.state, map: { ...prev.state.map, viewport: { ...prev.state.map.viewport, pitch: 30 } } },
    }));
    renderMapView();
    fireEvent.click(screen.getAllByRole("button", { name: /Height/ })[0]);
    expect(useStore.getState().state.map.viewport.pitch).toBe(30);
  });
});
